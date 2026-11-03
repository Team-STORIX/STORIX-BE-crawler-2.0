"""신작 주간 수집 (#158). 요일마다 플랫폼 하나의 신작 탭을 긁어 노션 요청 처리와 같은 흐름으로 prod 까지 반영한다.

    python cli.py new-works --env prod                      # 오늘 요일의 플랫폼 (WEEKLY_NEW_SCHEDULE)
    python cli.py new-works --env prod --platform kakao_page
    python cli.py new-works --env dev --dry-run             # 수집 · 적재까지만. BE 에 보내지 않는다

1. 신작 탭 목록 → 수집 이력(works_source)에 성공 기록이 있는 작품은 상세를 열지 않는다
   (노션 요청 · 제목 검색 · 링크 등 어느 경로로 긁었든 이력에 남는다. 실패 기록만 있으면 다시 수집)
2. 남은 작품 상세 수집 → staging 적재 (load_run) → BE 반영 (run_import)
   분포 보류 중 건수 급변만 풀고, 파서 깨짐이 의심되는 보류(작가 빈 값 · 연령 쏠림 · 장르 하나)는 보내지 않는다
3. 실패(세션 만료 · 차단 · 목록 0건 · 보류 유지)만 슬랙으로 알린다. 새로 만든 작품은 월요일 주간 알림에 나온다
"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from config import WEEKLY_NEW_SCHEDULE
from crawler import report
from crawler.modes import new_works

SOURCE = 'new_works_{}'   # 플랫폼마다 따로 — 직전 런과 건수를 비교할 단위
COUNT_HOLD = '수집 건수'   # review.rules.check_run 의 건수 급변 보류 사유 앞부분


def platform_for(today=None) -> str | None:
    today = today or datetime.now(ZoneInfo('Asia/Seoul')).date()
    return WEEKLY_NEW_SCHEDULE.get(today.weekday())


def suspicious_holds(reasons: list[str]) -> list[str]:
    """보류 사유 중 풀면 안 되는 것. 건수 급변은 신작 탭에선 자연스러워 뺀다"""
    return [r for r in reasons if not r.startswith(COUNT_HOLD)]


def known_filter(store):
    """목록 링크 → 이미 긁은 링크 집합"""
    from review.landing import canonical_landing_url, platform_work_key

    def known(urls: list[str]) -> set[str]:
        keys = {u: platform_work_key(canonical_landing_url(u)) for u in urls}
        collected = store.collected_keys([k for k in keys.values() if k])
        return {u for u, k in keys.items() if k in collected}
    return known


def run_weekly_new(platform: str | None = None, dry_run: bool = False) -> dict:
    from review.app import BACKEND, BACKEND_MISSING, load_catalog
    from review.importer import BackendClient, run_import
    from review.service import load_run, read_jsonl
    from review.store import StagingStore, connect, ensure_schema

    platform = platform or platform_for()
    if not platform:
        print('📅 오늘은 신작 수집 요일이 아닙니다')
        return {'platform': None}
    if platform not in new_works.SUPPORTED_PLATFORMS:
        print(f'⚠️  {platform} 신작 목록 수집기가 아직 없습니다 (지원: {", ".join(new_works.SUPPORTED_PLATFORMS)})')
        return {'platform': platform, 'supported': False}

    ensure_schema()
    conn = connect()
    try:
        store = StagingStore(conn)
        files = new_works.run_new_works(platform, known=known_filter(store))
        summary = {'platform': platform, **new_works.STATS.get(platform, {}), 'runs': []}
        if not files:
            print(f'📭 새로 수집한 작품 없음: {json.dumps(summary, ensure_ascii=False)}')
            return summary
        catalog = load_catalog()
        for f in files:
            result = load_run(store, catalog, read_jsonl(f), SOURCE.format(platform), str(f))
            print(f'📥 {f.name}: {json.dumps(result, ensure_ascii=False)}')
            run = {'run_id': result['run_id'], 'by_status': result['by_status']}
            suspicious = suspicious_holds(result['hold_reasons'])
            if suspicious:
                # 파서가 깨진 듯한 런(작가 빈 값 · 연령 쏠림 · 장르 하나)은 보내지 않고 알린다. 사람이 보고 stage release
                report.record(platform, report.PARSING_FAILED, result['run_id'], ' / '.join(suspicious))
                print(f"⛔ 보류 유지 — {' / '.join(suspicious)}")
                run['held'] = suspicious
                summary['runs'].append(run)
                continue
            if result['held']:
                # 건수 급변만 있으면 푼다 — 신작 탭은 주마다 건수가 달라 자연스럽다
                store.release_run(result['run_id'], 'new_works')
            if not dry_run:
                if BACKEND is None:
                    raise RuntimeError(BACKEND_MISSING)
                run['import'] = run_import(store, BackendClient(BACKEND), 500, result['run_id'])
                print(f"📤 {json.dumps(run['import'], ensure_ascii=False)}")
            summary['runs'].append(run)
        return summary
    finally:
        conn.close()
