"""requests 모드: 노션 '작품 추가 요청'(랜딩 폼)을 자동 처리한다 (#66).

    python cli.py requests --env prod                 # 수집 → 검수 적재 → BE 반영 → 노션 표시
    python cli.py requests --env prod --dry-run       # 수집 · 검수 적재까지만. BE · 노션은 건드리지 않는다
    python cli.py requests --env prod --resume        # dry-run 을 이어받아 검수 제안 반영 → BE 반영 → 노션 표시 (#68)

1. 노션에서 '추가 여부' · '적재 검토 중' 이 둘 다 꺼진 요청을 읽는다 (NOTION_REQUEST_MIN_ID 부터)
2. 요청의 연재처 · 작품 형태로 작품명 검색 → 수집 (search_titles 흐름, 출력 <플랫폼>_requests.jsonl)
3. staging 적재 → BE import. 이미 있는 작품이면 BE 가 갱신(UPDATED)하고 비어 있던 플랫폼 링크가 채워진다
4. 결과를 노션에 표시
   - 수집한 작품이 하나라도 BE 에 들어감 → '추가 여부' 체크
   - 그 외(못 찾음 · 검수 대기 · 중복 의심 · 실패) → '적재 검토 중' 체크 + '메모' 에 이유
   - 단, 찾을 연재처의 세션이 만료돼 못 찾은 요청은 그대로 둔다 — 다시 로그인하면 다음 실행에서 다시 찾는다
"""
import json
import os
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from config import OUTPUT_DIR, RUN_DIR
from modules.crawler import platforms
from modules.crawler.classify import REQUEST_SECTIONS
from review.notion import DEFAULT_MIN_ID, NotionError, NotionRequests, Request

SOURCE = 'notion_requests'
MODE = 'requests'

# 노션 '연재처' → 크롤러 플랫폼 (선택지: 네이버 웹툰 · 네이버 시리즈 · 카카오페이지 · 리디북스 · 봄툰 · 레진코믹스 · 미스터블루 · 기타)
# 카카오 웹툰은 카카오페이지로 합쳐져 선택지에서 빠졌다 (#107)
PLATFORM_KEYS = {
    '네이버 웹툰': ['naver_webtoon'],
    '네이버 시리즈': ['naver_series'],
    '카카오페이지': ['kakao_page'],
    '리디북스': ['ridibooks'],
    '레진코믹스': ['lezhin'],
    '미스터블루': ['mrblue'],
}
# 수집하지 않는 연재처. 이것만 적힌 요청은 다른 플랫폼에서 찾지 않고 이유를 남긴다 — 엉뚱한 작품이 걸리지 않게 (#131)
NOT_CRAWLED = {'봄툰': '봄툰은 robots.txt 가 막은 경로라 수집하지 않습니다'}
# '기타' 만 적혔을 때 찾아볼 곳
FALLBACK_KEYS = list(platforms.GENERAL_SEARCH)


def plan(requests: list[Request]) -> dict[str, list[tuple[str, str | None]]]:
    """요청들 → 플랫폼별 (작품명, 섹션) 목록."""
    out: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
    for r in requests:
        keys = []
        for p in r.platforms:
            keys += PLATFORM_KEYS.get(p, [])
        # '기타' 는 표에 없는 연재처라는 뜻이라, 다른 연재처와 같이 적혀도 전체에서 찾는다
        skipped = [p for p in r.platforms if p in NOT_CRAWLED]
        if '기타' in r.platforms or (not keys and not skipped):
            keys += FALLBACK_KEYS
        keys = list(dict.fromkeys(keys))
        sections = [s for t in r.types for s in REQUEST_SECTIONS.get(t, ())] or [None]
        for key in keys:
            for sec in dict.fromkeys(sections):
                if (r.title, sec) not in out[key]:
                    out[key].append((r.title, sec))
    return dict(out)


@dataclass
class Outcome:
    done: bool
    note: str
    works_ids: list[int] = field(default_factory=list)
    retry: bool = False   # 노션에 표시하지 않고 다음 실행에서 다시 찾는다


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
    from review.app import load_catalog
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

    # 요청 제목 → 수집한 작품 링크
    found: dict[str, list[str]] = defaultdict(list)

    def on_found(title: str, result: dict) -> None:
        found[title.strip()].append(result['source_url'])

    for platform, titles in plan(requests).items():
        run_search_titles(platform, titles, mode=MODE, on_found=on_found)

    # 수집한 게 없으면 DB 에 붙지 않는다
    day_dir = RUN_DIR
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
        if dry_run:
            # --resume 이 다시 수집하지 않고 이어받도록 요청 · 수집 링크 · 런을 남긴다 (#68)
            _report(store, requests, found)
            summary['state'] = str(save_state(day_dir, target, min_id, requests, found, summary['runs']))
            print(f"💾 이어받기 상태: {summary['state']}")
            print(f'   반영: python cli.py requests --env {target} --resume')
            return summary
        _finish(store, notion, requests, found, summary)
    finally:
        if conn:
            conn.close()
    return summary


def resume_requests(target: str, state_path: str | None = None) -> dict:
    """dry-run 을 이어받아 반영한다 (#68). 다시 수집하지 않는다.
    1) 붙은 검수 제안을 반영 (approve · reject)  2) BE 반영  3) 노션 표시. 노션에서 그사이 처리된 요청은 건너뛴다."""
    from review.app import load_catalog
    from review.service import apply_suggestions
    from review.store import StagingStore, connect, ensure_schema

    path = Path(state_path) if state_path else latest_state(target)
    if not path:
        raise NotionError(f'이어받을 dry-run 이 없습니다 ({target}). 먼저 --dry-run 을 실행하세요')
    state = json.loads(path.read_text(encoding='utf-8'))
    if state.get('target') != target:
        raise NotionError(f"상태 파일 대상이 {state.get('target')} 입니다 (지금 {target}): {path}")
    if state.get('resumed_at'):
        raise NotionError(f"이미 반영한 dry-run 입니다 ({state['resumed_at']}): {path}")

    notion = NotionRequests()
    still_pending = {r.id for r in notion.pending(state['min_id'])}
    requests = [r for r in (Request(**r) for r in state['requests']) if r.id in still_pending]
    found = defaultdict(list, state['found'])
    print(f"📂 {path} — 요청 {len(requests)}건 (그사이 처리된 {len(state['requests']) - len(requests)}건 제외), "
          f"런 {len(state['runs'])}개")
    summary = {'requests': len(requests), 'done': 0, 'review': 0, 'runs': state['runs']}

    conn = store = None
    try:
        if state['runs']:
            ensure_schema()
            conn = connect()
            store = StagingStore(conn)
            suggested = apply_suggestions(store, load_catalog(), state['runs'])
            summary['suggestions'] = suggested
            print(f'🧾 검수 제안 반영: {json.dumps(suggested, ensure_ascii=False)}')
        _finish(store, notion, requests, found, summary)
    finally:
        if conn:
            conn.close()
    state['resumed_at'] = datetime.now().isoformat(timespec='seconds')
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    return summary


def _finish(store, notion: NotionRequests, requests: list[Request], found: dict[str, list[str]], summary: dict) -> None:
    from review.app import BACKEND, BACKEND_MISSING
    from review.importer import BackendClient, run_import

    if store:
        for run_id in summary['runs']:
            if BACKEND is None:
                raise RuntimeError(BACKEND_MISSING)
            imported = run_import(store, BackendClient(BACKEND), 500, run_id)
            print(f'📤 {json.dumps(imported, ensure_ascii=False)}')

    for r, outcome in _report(store, requests, found):
        if outcome.retry:
            summary['retry'] = summary.get('retry', 0) + 1
        elif outcome.done:
            notion.mark_done(r)
            summary['done'] += 1
        else:
            notion.mark_review(r, outcome.note)
            summary['review'] += 1


def _report(store, requests: list[Request], found: dict[str, list[str]]) -> list[tuple[Request, Outcome]]:
    from crawler import report

    expired = {p for p, counts in report.summary()['by_platform'].items() if report.AUTH_EXPIRED in counts}
    out = []
    for r in requests:
        urls = list(dict.fromkeys(found.get(r.title, [])))
        planned = plan([r])
        blocked = sorted(expired & planned.keys())
        if not planned:
            outcome = Outcome(False, ' · '.join(NOT_CRAWLED[p] for p in r.platforms if p in NOT_CRAWLED))
        elif not urls and blocked:
            outcome = Outcome(False, f"세션 만료({', '.join(blocked)})로 못 찾음 — 다시 로그인 뒤 다음 실행에서 다시 찾습니다",
                              retry=True)
        else:
            outcome = decide(urls, store.rows_by_source_urls(urls) if store and urls else [])
        label = '⏳ 보류' if outcome.retry else '✅ 추가' if outcome.done else '🔎 검토'
        print(f"{label} #{r.id} {r.title} — {outcome.note}")
        out.append((r, outcome))
    return out


# dry-run 상태 파일 (#68). output/<날짜>/requests_state_<env>.json

def state_file(day_dir: Path, target: str) -> Path:
    return Path(day_dir) / f'{MODE}_state_{target}.json'


def save_state(day_dir: Path, target: str, min_id: int, requests: list[Request],
               found: dict[str, list[str]], runs: list[str]) -> Path:
    path = state_file(day_dir, target)
    path.parent.mkdir(parents=True, exist_ok=True)
    titles = {r.title for r in requests}
    path.write_text(json.dumps({
        'target': target,
        'created_at': datetime.now().isoformat(timespec='seconds'),
        'min_id': min_id,
        'requests': [asdict(r) for r in requests],
        'found': {t: list(dict.fromkeys(u)) for t, u in found.items() if t in titles},
        'runs': runs,
        'resumed_at': None,
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


def latest_state(target: str, output_dir: Path | None = None) -> Path | None:
    files = sorted(Path(output_dir or OUTPUT_DIR).glob(f'*/{MODE}_state_{target}.json'),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files:
        try:
            if not json.loads(f.read_text(encoding='utf-8')).get('resumed_at'):
                return f
        except (OSError, json.JSONDecodeError):
            continue
    return None
