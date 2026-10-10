"""requests 모드: 노션 '작품 추가 요청'(랜딩 폼)을 자동 처리한다 (#66).

    python cli.py requests --env prod                 # 수집 → 검수 적재 → BE 반영 → 노션 표시
    python cli.py requests --env prod --dry-run       # 수집 · 검수 적재까지만. BE · 노션은 건드리지 않는다

1. 노션에서 '추가 여부' · '적재 검토 중' 이 둘 다 꺼진 요청을 읽는다 (NOTION_REQUEST_MIN_ID 부터)
2. 요청의 연재처 · 작품 형태로 작품명 검색 → 수집 (search_titles 흐름, 출력 <플랫폼>_requests.jsonl)
3. staging 적재 → BE import. 이미 있는 작품이면 BE 가 갱신(UPDATED)하고 비어 있던 플랫폼 링크가 채워진다
4. 결과를 노션에 표시
   - 수집한 작품이 하나라도 BE 에 들어감 → '추가 여부' 체크
   - 그 외(못 찾음 · 검수 대기 · 중복 의심 · 실패) → '적재 검토 중' 체크 + '메모' 에 이유
"""
import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from config import OUTPUT_DIR
from review.notion import DEFAULT_MIN_ID, NotionRequests, Request

SOURCE = 'notion_requests'
MODE = 'requests'

# 노션 '연재처' → 크롤러 플랫폼. 검색을 못 하는 플랫폼(레진 · 미스터블루)은 다른 연재처로 찾는다
PLATFORM_KEYS = {
    '네이버 웹툰': ['naver_webtoon'],
    '네이버 시리즈': ['naver_series'],
    '카카오페이지': ['kakao_page'],
    '카카오 웹툰': ['kakao_page'],   # 카카오 웹툰 작품은 대부분 카카오페이지에도 있다
    '리디북스': ['ridibooks'],
    '봄툰': ['bomtoon'],
}
# '기타' · 검색 못 하는 연재처만 적혔을 때 찾아볼 곳
FALLBACK_KEYS = ['naver_webtoon', 'naver_novel', 'naver_series', 'kakao_page', 'ridibooks']
# 노션 '작품 형태' → 목록 섹션 (cli._TYPE_HEADERS). 단행본은 웹소설로 찾는다 (e북을 대신 고르는 규칙)
TYPE_SECTIONS = {'웹툰': '웹툰', '웹소설': '웹소설', '단행본': '웹소설', '만화': '만화'}
# 웹소설 요청이 네이버 쪽이면 네이버 웹소설도 본다
NOVEL_EXTRA = {'naver_series': 'naver_novel'}


def plan(requests: list[Request]) -> dict[str, list[tuple[str, str | None]]]:
    """요청들 → 플랫폼별 (작품명, 섹션) 목록."""
    out: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
    for r in requests:
        keys = []
        for p in r.platforms:
            keys += PLATFORM_KEYS.get(p, [])
        keys = list(dict.fromkeys(keys)) or FALLBACK_KEYS
        sections = [TYPE_SECTIONS[t] for t in r.types if t in TYPE_SECTIONS] or [None]
        for key in keys:
            for sec in dict.fromkeys(sections):
                targets = [key] + ([NOVEL_EXTRA[key]] if sec == '웹소설' and key in NOVEL_EXTRA else [])
                for k in targets:
                    if (r.title, sec) not in out[k]:
                        out[k].append((r.title, sec))
    return dict(out)


@dataclass
class Outcome:
    done: bool
    note: str
    works_ids: list[int] = field(default_factory=list)


def decide(urls: list[str], rows: list[dict]) -> Outcome:
    """요청 하나의 수집 링크 · staging 행 → 노션에 표시할 결과."""
    if not urls:
        return Outcome(False, '연재처에서 작품을 찾지 못했습니다')
    imported = sorted({r['imported_works_id'] for r in rows if r['status'] == 'IMPORTED' and r['imported_works_id']})
    if imported:
        return Outcome(True, f"prod 작품 {', '.join(map(str, imported))}", imported)
    reasons = []
    for r in rows:
        codes = [v.get('code') for v in (r.get('violations') or []) if v.get('severity') != 'INFO']
        if r.get('import_error'):
            codes.append('BE 오류: ' + r['import_error'][:80])
        reasons.append(f"{r['works_name']}({r['works_type']}) {r['status']} {', '.join(c for c in codes if c)}".strip())
    return Outcome(False, '검수 필요 — ' + ' / '.join(reasons) if reasons else '수집했지만 적재 기록이 없습니다')


def run_requests(target: str, min_id: int | None = None, dry_run: bool = False, limit: int | None = None) -> dict:
    from crawler.modes.search_titles import run_search_titles
    from review.app import BACKEND, BACKEND_MISSING, load_catalog
    from review.importer import BackendClient, run_import
    from review.service import load_run, read_jsonl
    from review.store import StagingStore, connect, ensure_schema

    min_id = min_id or int(os.getenv('NOTION_REQUEST_MIN_ID') or DEFAULT_MIN_ID)
    notion = NotionRequests()
    requests = notion.pending(min_id)[:limit] if limit else notion.pending(min_id)
    print(f'📮 노션 요청 {len(requests)}건 (ID {min_id}~)')
    for r in requests:
        print(f'   #{r.id} {r.title} | {r.types} | {r.platforms}')
    if not requests:
        return {'requests': 0}

    # 1) 수집. 요청 제목 → 수집한 작품 링크
    found: dict[str, list[str]] = defaultdict(list)

    def on_found(title: str, result: dict) -> None:
        found[title.strip()].append(result['source_url'])

    for platform, titles in plan(requests).items():
        run_search_titles(platform, titles, mode=MODE, on_found=on_found)

    # 2) 검수 적재 → BE 반영. 수집한 게 없으면 DB 에 붙지 않는다
    day_dir = Path(OUTPUT_DIR) / datetime.now().strftime('%Y-%m-%d')
    files = sorted(day_dir.glob(f'*_{MODE}.jsonl')) if found else []
    summary = {'requests': len(requests), 'done': 0, 'review': 0, 'runs': []}
    conn = store = None
    try:
        if files:
            ensure_schema()
            conn = connect()
            store = StagingStore(conn)
            catalog = load_catalog()
            for f in files:
                result = load_run(store, catalog, read_jsonl(f), SOURCE, str(f))
                if result['held']:
                    # 요청 목록은 사람이 고른 작품이라 분포 이상 보류(연령 · 장르 쏠림)는 풀고 보낸다
                    store.release_run(result['run_id'], 'notion_requests')
                summary['runs'].append(result['run_id'])
                print(f'📥 {f.name}: {json.dumps(result, ensure_ascii=False)}')
                if not dry_run:
                    if BACKEND is None:
                        raise RuntimeError(BACKEND_MISSING)
                    imported = run_import(store, BackendClient(BACKEND), 500, result['run_id'])
                    print(f'📤 {json.dumps(imported, ensure_ascii=False)}')

        # 3) 노션 표시
        for r in requests:
            urls = list(dict.fromkeys(found.get(r.title, [])))
            outcome = decide(urls, store.rows_by_source_urls(urls) if store and urls else [])
            mark = '✅ 추가' if outcome.done else '🔎 검토'
            print(f'{mark} #{r.id} {r.title} — {outcome.note}')
            if dry_run:
                continue
            if outcome.done:
                notion.mark_done(r)
                summary['done'] += 1
            else:
                notion.mark_review(r, outcome.note)
                summary['review'] += 1
    finally:
        if conn:
            conn.close()
    return summary
