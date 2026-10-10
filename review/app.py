"""검수 서비스 (Layer 1 · 1.5 + 사람 검수 + BE import).

실행 (로컬 전용. 배포는 물량이 늘면 그때):
    uvicorn review.app:app --host 127.0.0.1 --port 8200

환경변수
    STORIX_ENV            BE 대상 dev | prod (기본 dev). 주소 · 키 · staging DB 가 따라 바뀐다
    STORIX_{DEV,PROD}_INTERNAL_API_KEY  BE 내부 API 키 (X-Internal-Api-Key 헤더)
    REVIEW_CATALOG_FILE   BE 를 못 붙을 때 카탈로그 응답을 저장한 JSON 파일 (선택)
    REVIEW_API_TOKEN      설정하면 X-Review-Token 헤더가 맞아야 호출된다 (선택)
    STAGING_DATABASE_NAME staging DB 이름 (기본 storix_staging_{env})
"""
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from review import env as storix_env
from review import service
from review.backend import BackendAuthError, BackendSession
from review.catalog import EnumCatalog, fetch_catalog, load_catalog_file
from review.importer import BackendClient, run_import
from review.rules import NEEDS_REVIEW
from review.store import StagingStore, connect, ensure_schema

API_TOKEN = os.getenv('REVIEW_API_TOKEN', '')
BACKEND = BackendSession.from_env()
BACKEND_MISSING = f'STORIX_{storix_env.target().upper()}_INTERNAL_API_KEY 미설정 (STORIX_ENV={storix_env.target()})'

_state: dict = {}


def load_catalog() -> EnumCatalog:
    path = os.getenv('REVIEW_CATALOG_FILE')
    if path:
        return load_catalog_file(path)
    if BACKEND is None:
        raise RuntimeError(f'{BACKEND_MISSING} (또는 REVIEW_CATALOG_FILE)')
    return fetch_catalog(BACKEND)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_schema()
    _state['catalog'] = load_catalog()
    yield


app = FastAPI(title='STORIX 작품 검수', lifespan=lifespan)


def _auth(x_review_token: str = Header(default='')):
    if API_TOKEN and x_review_token != API_TOKEN:
        raise HTTPException(status_code=401, detail='invalid token')


def _store():
    conn = connect()
    try:
        yield StagingStore(conn)
    finally:
        conn.close()


def _catalog() -> EnumCatalog:
    return _state['catalog']


class RunRequest(BaseModel):
    source: str = Field(..., min_length=1, max_length=100)
    source_file: str | None = None
    items: list[dict]


class ApproveRequest(BaseModel):
    reviewer: str = Field(..., min_length=1, max_length=100)
    # 고칠 필드만 보낸다. enum 은 BE name 이나 한글 dbValue 둘 다 된다
    overrides: dict[str, str | list[str] | None] = Field(default_factory=dict)
    # 중복 의심 건: 기존 작품에 붙일지(target_works_id), 새로 만들지(create_new) 고른다
    target_works_id: int | None = Field(default=None, ge=1)
    create_new: bool = False


class RejectRequest(BaseModel):
    reviewer: str = Field(..., min_length=1, max_length=100)
    reason: str = Field(..., min_length=1, max_length=500)


class ReleaseRequest(BaseModel):
    reviewer: str = Field(..., min_length=1, max_length=100)


@app.get('/health')
def health():
    return {'ok': True, 'catalog_loaded': 'catalog' in _state}


@app.get('/catalog', dependencies=[Depends(_auth)])
def catalog(cat: EnumCatalog = Depends(_catalog)):
    return cat.raw


@app.post('/runs', dependencies=[Depends(_auth)])
def create_run(req: RunRequest, store: StagingStore = Depends(_store), cat: EnumCatalog = Depends(_catalog)):
    """크롤 산출물 적재 + Layer 1 · 1.5 판정. (로드맵의 POST /validate)"""
    return service.load_run(store, cat, req.items, req.source, req.source_file)


@app.get('/runs', dependencies=[Depends(_auth)])
def list_runs(limit: int = Query(20, ge=1, le=200), store: StagingStore = Depends(_store)):
    return store.list_runs(limit)


@app.post('/runs/{run_id}/release', dependencies=[Depends(_auth)])
def release_run(run_id: str, req: ReleaseRequest, store: StagingStore = Depends(_store)):
    if not store.release_run(run_id, req.reviewer):
        raise HTTPException(status_code=404, detail='보류 중인 런이 아님')
    return {'run_id': run_id, 'held': False}


@app.get('/review/queue', dependencies=[Depends(_auth)])
def review_queue(status: str = NEEDS_REVIEW,
                 limit: int = Query(50, ge=1, le=500),
                 offset: int = Query(0, ge=0),
                 store: StagingStore = Depends(_store)):
    return store.queue(status, limit, offset)


@app.get('/review/{staging_id}', dependencies=[Depends(_auth)])
def review_item(staging_id: int, store: StagingStore = Depends(_store)):
    row = store.get(staging_id)
    if not row:
        raise HTTPException(status_code=404, detail='staging 항목 없음')
    return row


@app.post('/review/{staging_id}/approve', dependencies=[Depends(_auth)])
def approve(staging_id: int, req: ApproveRequest,
            store: StagingStore = Depends(_store), cat: EnumCatalog = Depends(_catalog)):
    try:
        return service.approve(store, cat, staging_id, req.overrides, req.reviewer,
                               req.target_works_id, req.create_new)
    except service.ReviewError as e:
        raise HTTPException(status_code=422, detail={'message': str(e), 'violations': e.violations})


@app.post('/review/{staging_id}/reject', dependencies=[Depends(_auth)])
def reject(staging_id: int, req: RejectRequest, store: StagingStore = Depends(_store)):
    try:
        return service.reject(store, staging_id, req.reviewer, req.reason)
    except service.ReviewError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get('/import/preview', dependencies=[Depends(_auth)])
def import_preview(run_id: str | None = None, store: StagingStore = Depends(_store)):
    """POST /import 가 보낼 건수를 런별로. 보내기 전에 확인한다."""
    return store.importable_summary(run_id)


@app.post('/import', dependencies=[Depends(_auth)])
def import_to_backend(run_id: str | None = None,
                      all_runs: bool = False,
                      limit: int = Query(500, ge=1, le=5000),
                      store: StagingStore = Depends(_store)):
    """AUTO_PASS · APPROVED 를 BE import API 로 승격. 보류된 런은 빠진다.
    run_id 나 all_runs=true 중 하나를 꼭 줘야 한다 — 의도하지 않은 런이 같이 나가지 않게."""
    if not run_id and not all_runs:
        raise HTTPException(status_code=400, detail='run_id 또는 all_runs=true 가 필요합니다. 먼저 GET /import/preview 로 확인하세요')
    if BACKEND is None:
        raise HTTPException(status_code=503, detail=BACKEND_MISSING)
    try:
        return run_import(store, BackendClient(BACKEND), limit, run_id)
    except BackendAuthError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get('/sources', dependencies=[Depends(_auth)])
def list_sources(status: str | None = Query(None, description="broken(복구 대상) · SUCCESS · RELINKED · DETAIL_NOT_FOUND …"),
                 platform: str | None = Query(None, description='BE Platform enum name (예: RIDIBOOKS)'),
                 works_id: int | None = Query(None, ge=1),
                 limit: int = Query(50, ge=1, le=500),
                 offset: int = Query(0, ge=0),
                 store: StagingStore = Depends(_store)):
    """작품별 수집 이력 (#28). 연속 실패가 많은 것부터. 링크 복구 실행은 CLI(stage recover)로 한다."""
    return store.list_sources(status, platform, works_id, limit, offset)


@app.get('/sources/{platform}/{platform_work_id}', dependencies=[Depends(_auth)])
def get_source(platform: str, platform_work_id: str, store: StagingStore = Depends(_store)):
    row = store.get_source_by_key(platform, platform_work_id)
    if not row:
        raise HTTPException(status_code=404, detail='수집 이력 없음')
    return row


@app.get('/stats', dependencies=[Depends(_auth)])
def stats(store: StagingStore = Depends(_store)):
    return store.stats()
