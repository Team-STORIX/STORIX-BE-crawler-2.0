import argparse
import glob
import sys
from pathlib import Path

import config  # noqa: F401  (stdout/stderr UTF-8 재설정 + .env 로드)
from modules.crawler.classify import REQUEST_SECTIONS


# 섹션 → 찾을 유형은 노션 요청과 같은 표 (classify.REQUEST_SECTIONS). 이 유형은 DB 의 works_type 이 아니라
# '어느 플랫폼에서 검색할지' 고르는 필터다 — 적재되는 works_type 은 크롤러가 상세에서 다시 판정한다
def _load_titles(args) -> list[tuple[str, str | None]]:
    titles_file = getattr(args, 'titles_file', None)
    titles_str = getattr(args, 'titles', None)

    if titles_file:
        path = Path(titles_file)
        if not path.exists():
            print(f'❌ 파일을 찾을 수 없습니다: {path}')
            sys.exit(1)
        return _parse_titles_file(path)

    if titles_str:
        # 인라인 목록은 타입 정보가 없음 → None (대상 플랫폼 무관하게 검색)
        seen: set[str] = set()
        inline: list[tuple[str, str | None]] = []
        for t in titles_str.split(','):
            t = t.strip()
            if t and t not in seen:
                seen.add(t)
                inline.append((t, None))
        return inline

    return []


def _parse_titles_file(path: Path) -> list[tuple[str, str | None]]:
    """섹션 헤더 형식 파싱.

        ## 웹툰            ← 이 아래 제목은 모두 '웹툰' 타입
        내가 키운 S급들
        ## 웹소설
        마법학교 마법사로 살아가는 법
        ## 전체            ← 웹툰판 · 웹소설판을 각각 찾아 있는 판을 다 수집
        패밀리 레스토랑 가자

    같은 섹션에 같은 제목이 여러 번 있으면 첫 줄만 쓴다(같은 작품을 두 번 크롤하지 않도록).
    다른 섹션에 같은 제목이 있는 건 별개 레코드일 수 있으므로 그대로 둔다.
    """
    result: list[tuple[str, str | None]] = []
    seen: set[tuple[str, str]] = set()
    dupes = 0
    current_types: tuple[str, ...] | None = None

    # utf-8-sig: 메모장 등으로 편집해 BOM 이 붙어도 첫 줄이 깨지지 않게
    for raw in path.read_text(encoding='utf-8-sig').splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith('##'):
            header = line.lstrip('#').strip()
            current_types = REQUEST_SECTIONS.get(header)
            if current_types is None:
                print(f'⚠️  알 수 없는 섹션 헤더 무시: "{line}" '
                      f'({" | ".join(REQUEST_SECTIONS)} 만 지원)')
            continue
        if line.startswith('#'):
            continue
        if current_types is None:
            print(f'⚠️  타입 헤더 없이 나온 제목 스킵: "{line}" '
                  f'({" / ".join("## " + h for h in REQUEST_SECTIONS)} 아래에 배치)')
            continue
        for current_type in current_types:
            key = (line, current_type)
            if key in seen:
                dupes += 1
                continue
            seen.add(key)
            result.append((line, current_type))

    if dupes:
        print(f'ℹ️  중복 제목 {dupes}개 제외 — 검색 대상 {len(result)}개')

    return result


def cmd_login(args):
    from modules.crawler import platforms

    platform = args.platform
    targets = ['naver_webtoon', 'kakao_page', 'ridibooks'] if platform == 'all' else [platform]

    for name in targets:
        try:
            p = platforms.get(name)
        except KeyError:
            print(f'❌ 지원하지 않는 플랫폼: {name} ({" | ".join(x.key for x in platforms.PLATFORMS)})')
            continue
        # 네이버 웹소설 · 시리즈는 네이버 웹툰 로그인 쿠키를 같이 쓴다
        p = platforms.get(p.login)
        print(f'\n{"="*60}')
        print(f'🔐 [{p.key}] 로그인 세션 저장')
        print(f'{"="*60}')

        cookie_file = platforms.cookie_file(p.key)
        if cookie_file.exists():
            cookie_file.unlink()
            print(f'🗑️  기존 세션 파일 삭제: {cookie_file.name}')

        crawler = platforms.crawler_class(p.key)()
        crawler.block_images = False  # 보안문자 · 로그인 화면을 사람이 봐야 한다
        crawler.start_driver()
        try:
            if crawler.login():
                print(f'✅ [{p.key}] 로그인 성공. 쿠키가 sessions/ 에 저장되었습니다.')
            else:
                print(f'❌ [{p.key}] 로그인 실패.')
        finally:
            crawler.close_driver()


def cmd_keepalive(args):
    """로그인 쿠키를 열어 갱신한다. 만료된 플랫폼은 슬랙으로 알린다 (다시 로그인 버튼)."""
    import json

    from config import RUN_DIR
    from crawler import notify, report
    from crawler.modes.keepalive import run_keepalive

    report.reset()
    result = run_keepalive(args.platform)
    report.save(RUN_DIR, 'keepalive')
    notify.notify_report('keepalive')
    print(json.dumps(result, ensure_ascii=False))
    if report.failed():
        sys.exit(1)


def cmd_crawl(args):
    mode = args.mode
    platform = args.platform

    if mode != 'custom_url' and not platform:
        print('❌ custom_url 제외한 모드는 --platform이 필수입니다.')
        sys.exit(1)

    if mode == 'initial':
        from crawler.modes.initial import run_initial, SUPPORTED_PLATFORMS
        if platform != 'all' and platform not in SUPPORTED_PLATFORMS:
            print(f'❌ 지원하지 않는 플랫폼: {platform}')
            print(f'   지원 목록: {", ".join(SUPPORTED_PLATFORMS)} | all')
            sys.exit(1)
        run_initial(platform, args.parallel)

    elif mode == 'new_works':
        from crawler.modes.new_works import run_new_works, SUPPORTED_PLATFORMS
        if platform != 'all' and platform not in SUPPORTED_PLATFORMS:
            print(f'❌ 지원하지 않는 플랫폼: {platform}')
            print(f'   지원 목록: {", ".join(SUPPORTED_PLATFORMS)} | all')
            sys.exit(1)
        run_new_works(platform, args.parallel)

    elif mode == 'update_fields':
        from crawler.modes.update_fields import run_update_fields, SUPPORTED_PLATFORMS
        if platform != 'all' and platform not in SUPPORTED_PLATFORMS:
            print(f'❌ 지원하지 않는 플랫폼: {platform}')
            print(f'   지원 목록: {", ".join(SUPPORTED_PLATFORMS)} | all')
            sys.exit(1)
        if not args.input:
            print('❌ update_fields 모드는 --input 경로가 필요합니다.')
            print('   예: python cli.py crawl --platform all --mode update_fields --input ./output/2026-05-09/')
            sys.exit(1)
        run_update_fields(platform, args.input, args.parallel)

    elif mode == 'search_titles':
        from crawler.modes.search_titles import run_search_titles, SUPPORTED_PLATFORMS
        if platform != 'all' and platform not in SUPPORTED_PLATFORMS:
            print(f'❌ 지원하지 않는 플랫폼: {platform}')
            print(f'   지원 목록: {", ".join(SUPPORTED_PLATFORMS)} | all')
            sys.exit(1)
        titles = _load_titles(args)
        if not titles:
            print('❌ 검색할 작품명이 없습니다.')
            print('   --titles-file titles.txt  또는  --titles "작품A,작품B"')
            sys.exit(1)
        run_search_titles(platform, titles, titles_file=getattr(args, 'titles_file', None), parallel=args.parallel)

    elif mode == 'custom_url':
        from crawler.modes.custom_url import run_custom_url
        url = getattr(args, 'url', None)
        count = getattr(args, 'count', None)
        if not url:
            print('❌ custom_url 모드는 --url이 필수입니다.')
            print('   예: python cli.py crawl --mode custom_url --url "https://comic.naver.com/webtoon?tab=dailyPlus" --count 213')
            print('   지원 도메인: comic.naver.com(?titleId=<id> 단건 또는 탭 목록) | '
                  'series.naver.com(detail.series 단건) | '
                  'page.kakao.com(/content/<id> 단건 또는 목록) | '
                  'ridibooks.com(/books/<id> 단건 또는 카테고리)')
            sys.exit(1)
        run_custom_url(url, count)

    else:
        print(f'❌ 알 수 없는 모드: {mode}')
        sys.exit(1)


def cmd_requests(args):
    """노션 '작품 추가 요청' 자동 처리 (#66)."""
    import json
    import os

    if args.env:
        os.environ['STORIX_ENV'] = args.env  # review 모듈을 불러오기 전에 정한다
    from config import RUN_DIR
    from crawler import notify, report
    from crawler.modes.requests import resume_requests, run_requests
    from review import env as storix_env
    from review.notion import NotionError

    target = storix_env.target()
    mode = ' (dry-run)' if args.dry_run else ' (이어받기)' if args.resume else ''
    print(f'🎯 대상 {target.upper()} — BE {storix_env.api_base_url(target)}{mode}')
    try:
        if args.resume:
            summary = resume_requests(target, args.state)
        else:
            report.reset()
            summary = run_requests(target, args.min_id, args.dry_run, args.limit)
            report.save(RUN_DIR, 'requests')
            # 세션 만료 · 차단 · 런 실패는 슬랙으로 (#77). SLACK_* 가 없는 로컬 실행은 보내지 않는다
            notify.notify_report('requests')
    except NotionError as e:
        print(f'❌ {e}')
        notify.notify_error('requests', e)
        sys.exit(1)
    except Exception as e:
        notify.notify_error('requests', e)
        raise
    print(json.dumps(summary, ensure_ascii=False))
    if report.failed():
        sys.exit(1)


def cmd_stage(args):
    """검수 파이프라인. JSONL → works_staging(Layer 1 · 1.5) → BE import API."""
    import json
    import os

    # 대상 환경은 review 모듈을 불러오기 전에 정한다 (BE 주소 · 키 · staging DB 가 모듈 로드 때 정해진다)
    if getattr(args, 'env', None):
        os.environ['STORIX_ENV'] = args.env
    from review import env as storix_env
    try:
        target = storix_env.target()
    except ValueError as e:
        print(f'❌ {e}')
        sys.exit(1)

    from review.app import BACKEND, BACKEND_MISSING, load_catalog
    from review.backend import BackendAuthError
    from review.store import StagingStore, connect, ensure_schema

    sub = getattr(args, 'stage_command', None)
    if sub not in ('load', 'import', 'stats', 'recover', 'queue', 'suggest', 'promote', 'apply', 'weekly'):
        print('❌ stage 서브 명령이 없습니다. (load | import | stats | recover | queue | suggest | promote | apply | weekly)')
        sys.exit(1)

    from review.store import STAGING_DATABASE
    print(f'🎯 대상 {target.upper()} — BE {storix_env.api_base_url(target)} / staging DB {STAGING_DATABASE}')
    ensure_schema()
    conn = connect()
    try:
        store = StagingStore(conn)
        if sub == 'load':
            from review.service import load_run, read_jsonl, record_report_failures, report_for
            if args.catalog_file:
                os.environ['REVIEW_CATALOG_FILE'] = args.catalog_file
            try:
                catalog = load_catalog()
            except (BackendAuthError, OSError) as e:
                # OSError: BE 연결 실패(URLError) 포함
                print(f'❌ enum 카탈로그를 못 받았습니다: {e}')
                sys.exit(1)
            path = Path(args.input)
            if any(c in args.input for c in '*?['):  # 'output/2026-10-10/*_search_titles.jsonl'
                files = sorted(Path(f) for f in glob.glob(args.input))
            else:
                files = sorted(path.glob('*.jsonl')) if path.is_dir() else [path]
            files = [f for f in files if f.name != 'manual_review_queue.jsonl']
            if not files:
                print(f'❌ JSONL 파일이 없습니다: {path}')
                sys.exit(1)
            for f in files:
                result = load_run(store, catalog, read_jsonl(f), args.source, str(f))
                print(f'📥 {f.name}: {json.dumps(result, ensure_ascii=False)}')
                if result['held']:
                    print('⛔ 런 보류 — 분포 이상. 확인 후 POST /runs/{run_id}/release 로 풀어야 import 됩니다')
            # 같은 런의 상세 수집 실패를 작품별 수집 이력에 남긴다 (#28). 리포트는 런마다 하나라 한 번만 읽는다
            for report in dict.fromkeys(r for r in map(report_for, files) if r):
                print(f'🩹 {report.name}: {json.dumps(record_report_failures(store, report), ensure_ascii=False)}')
        elif sub == 'import':
            from review.importer import BackendClient, run_import
            if BACKEND is None:
                print(f'❌ {BACKEND_MISSING}')
                sys.exit(1)
            # 보내기 전에 런별 건수를 보여주고 확인받는다. 엉뚱한 런이 섞여 나가는 걸 막는다
            summary = store.importable_summary(args.run_id)
            total = sum(r['n'] for r in summary)
            if not total:
                print('보낼 작품이 없습니다.')
                return
            print(f'[{target.upper()}] BE({BACKEND.base_url}) 로 보낼 작품 {min(total, args.limit)}건 (최대 {args.limit}건)')
            for r in summary:
                print(f"  - {r['run_id']}  {r['source']:<24} {r['n']:>5}건  {r['source_file'] or ''}")
            if not args.yes:
                if not sys.stdin.isatty():
                    print('❌ 확인할 수 없는 환경입니다. 내용을 확인했다면 --yes 를 붙이세요')
                    sys.exit(1)
                if input('진행할까요? [y/N] ').strip().lower() != 'y':
                    print('취소했습니다.')
                    return
            try:
                print(json.dumps(run_import(store, BackendClient(BACKEND), args.limit, args.run_id),
                                 ensure_ascii=False))
            except BackendAuthError as e:
                print(f'❌ {e}')
                sys.exit(1)
        elif sub == 'queue':
            # 검수 대기 행을 바깥 검수(로컬 claude -p 등)에 넘길 JSON 으로 낸다 (#68)
            from review.service import review_export
            try:
                catalog = load_catalog()
            except (BackendAuthError, OSError, RuntimeError) as e:
                print(f'❌ enum 카탈로그를 못 받았습니다: {e}')
                sys.exit(1)
            if not args.all and not args.run_ids:
                print('❌ --run-id 나 --all 중 하나가 필요합니다')
                sys.exit(1)
            exported = review_export(store, catalog, None if args.all else args.run_ids, args.limit)
            Path(args.output).write_text(json.dumps(exported, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f"📤 검수 대기 {len(exported['rows'])}건 → {args.output}")
        elif sub == 'suggest':
            # 바깥 검수 제안을 llm_verdict 에 남긴다. 반영은 requests --resume 이 한다 (#68)
            from review.service import ReviewError, save_suggestions
            body = json.loads(Path(args.input).read_text(encoding='utf-8'))
            items = body.get('suggestions', []) if isinstance(body, dict) else body
            try:
                print(f'🧾 {json.dumps(save_suggestions(store, items, args.by), ensure_ascii=False)}')
            except ReviewError as e:
                print(f'❌ 제안 형식 오류 (아무것도 남기지 않음): {e}')
                sys.exit(1)
        elif sub == 'promote':
            # 사람 판정을 매핑으로 승격하고 지금 매핑을 보여준다 (#70). 적재(load) 때도 자동으로 돈다
            from review.service import PROMOTE_MIN
            min_count = args.min_count or PROMOTE_MIN
            result = store.promote_mappings(min_count)
            print(f"🧭 승격 기준: 같은 판정 {min_count}회 이상 · 결론이 모두 같음")
            print(json.dumps(result, ensure_ascii=False))
            for m in store.list_mappings():
                print(f"  {m['field']:<20} {m['raw_value']} → {m['decided_value']}  ({m['decision_count']}회)")
        elif sub == 'apply':
            # 바깥 검수 제안을 승인 · 거절로 반영하고, --import 면 이번에 승인한 건만 BE 로 보낸다 (#72)
            from review.importer import BackendClient, run_import
            from review.service import apply_suggestions
            if not args.all and not args.run_ids:
                print('❌ --run-id 나 --all 중 하나가 필요합니다')
                sys.exit(1)
            if args.do_import and BACKEND is None:
                print(f'❌ {BACKEND_MISSING}')
                sys.exit(1)
            try:
                catalog = load_catalog()
            except (BackendAuthError, OSError, RuntimeError) as e:
                print(f'❌ enum 카탈로그를 못 받았습니다: {e}')
                sys.exit(1)
            applied = apply_suggestions(store, catalog, None if args.all else args.run_ids)
            print(f'🧾 제안 반영: {json.dumps({k: v for k, v in applied.items() if k != "approved_ids"}, ensure_ascii=False)}')
            if args.do_import:
                ids = applied['approved_ids']
                if not ids:
                    print('보낼 작품이 없습니다 (이번에 승인된 건 없음)')
                    return
                try:
                    print(f'📤 {json.dumps(run_import(store, BackendClient(BACKEND), len(ids), staging_ids=ids), ensure_ascii=False)}')
                except BackendAuthError as e:
                    print(f'❌ {e}')
                    sys.exit(1)
        elif sub == 'recover':
            # 깨진 작품 링크를 다시 찾는다 (#28). 찾은 결과는 파일로 내고, 적재는 stage load 로 한다
            from config import RUN_DIR
            from crawler import report
            from crawler.modes.recover import run_recover
            report.reset()
            summary = run_recover(store, args.platform, args.limit)
            report.save(RUN_DIR, 'recover')
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            if summary['files']:
                print(f"   검수 적재: python cli.py stage load --env {target} "
                      f"--input '{RUN_DIR}/*_recover.jsonl' --source recover")
        elif sub == 'weekly':
            # 지난주(월~일) BE 에 새로 만든 작품을 슬랙으로 (#77). 서버는 월요일 자정 실행 때 --send 로 부른다
            from datetime import date
            from crawler import weekly
            result = weekly.run(store, date.fromisoformat(args.date) if args.date else None, send=args.send)
            print(result.pop('text') or '(지난주 새로 만든 작품 없음)')
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(json.dumps(store.stats(), ensure_ascii=False, indent=2))
    finally:
        conn.close()


def main():
    from modules.crawler import platforms

    parser = argparse.ArgumentParser(prog='cli.py', description='STORIX-BE-Crawler 2.0')
    subparsers = parser.add_subparsers(dest='command')

    crawl_p = subparsers.add_parser('crawl', help='플랫폼 크롤링 실행')
    crawl_p.add_argument('--platform', required=False,
                         help='naver_webtoon | kakao_page | ridibooks | all (custom_url 제외)')
    crawl_p.add_argument('--mode', required=True,
                         choices=['initial', 'new_works', 'update_fields', 'search_titles', 'custom_url'])
    crawl_p.add_argument('--input',
                         help='[update_fields 전용] 기존 JSONL 파일 또는 디렉토리 경로')
    crawl_p.add_argument('--titles-file',
                         help='[search_titles 전용] 작품명 목록 파일 (.txt, 줄바꿈 구분)',
                         dest='titles_file')
    crawl_p.add_argument('--titles',
                         help='[search_titles 전용] 작품명 목록 (쉼표 구분, 예: "작품A,작품B")')
    crawl_p.add_argument('--url',
                         help='[custom_url 전용] 크롤링할 URL (예: https://comic.naver.com/webtoon?tab=dailyPlus)')
    crawl_p.add_argument('--parallel', type=int, default=1,
                         help='[--platform all] 동시에 돌릴 플랫폼 수 (기본 1). 크롬은 이 수 × 플랫폼 워커 수만큼 뜬다')
    crawl_p.add_argument('--count',
                         type=int,
                         help='[custom_url 전용] 크롤링 개수 (기본값: 500개, 무한 스크롤 무시)')

    login_p = subparsers.add_parser('login', help='로그인 세션 저장 (Docker 실행 전 선행)')
    login_p.add_argument('--platform', required=True,
                         help='naver_webtoon | kakao_page | ridibooks | bomtoon | lezhin | mrblue | all (bomtoon · lezhin · mrblue 는 all 에 안 들어감). '
                              'BE enum(KAKAO_PAGE) · 네이버 웹소설 · 시리즈도 받는다')

    keep_p = subparsers.add_parser('keepalive', help='로그인 쿠키를 열어 갱신 · 만료면 슬랙 알림 (서버 야간 실행)')
    keep_p.add_argument('--platform', nargs='*', help='플랫폼 (기본: 쿠키 파일이 있는 플랫폼 전부)')

    stage_p = subparsers.add_parser('stage', help='검수 파이프라인 (works_staging → BE import API)')
    stage_sub = stage_p.add_subparsers(dest='stage_command')

    # 모든 stage 명령에 붙는다. 생략하면 STORIX_ENV(기본 dev)
    env_p = argparse.ArgumentParser(add_help=False)
    env_p.add_argument('--env', choices=('dev', 'prod'), help='BE 대상 환경 (기본 STORIX_ENV, 없으면 dev)')

    stage_load_p = stage_sub.add_parser('load', parents=[env_p], help='JSONL 을 staging 에 적재하고 Layer 1 · 1.5 판정')
    stage_load_p.add_argument('--input', required=True,
                              help="JSONL 파일 · 디렉토리 · 패턴 (예: 'output/2026-10-10/*_search_titles.jsonl')")
    stage_load_p.add_argument('--source', required=True,
                              help='직전 런과 건수를 비교할 단위 (예: naver_webtoon_initial)')
    stage_load_p.add_argument('--catalog-file', dest='catalog_file',
                              help='BE 대신 저장해 둔 enum 카탈로그 JSON 사용')

    stage_import_p = stage_sub.add_parser('import', parents=[env_p], help='AUTO_PASS · APPROVED 를 BE 로 승격')
    stage_import_p.add_argument('--limit', type=int, default=500)
    stage_import_p.add_argument('--run-id', dest='run_id', help='이 런만 보낸다 (생략하면 보류 안 된 전체 런)')
    stage_import_p.add_argument('--yes', action='store_true', help='확인 없이 진행 (건수를 이미 확인한 경우)')

    stage_sub.add_parser('stats', parents=[env_p], help='상태별 건수 · 자동화율')

    stage_weekly_p = stage_sub.add_parser('weekly', parents=[env_p], help='지난주 새로 만든 작품 알림 (#77)')
    stage_weekly_p.add_argument('--send', action='store_true', help='슬랙으로 보낸다 (없으면 출력만)')
    stage_weekly_p.add_argument('--date', help='이 날짜가 속한 주의 직전 주 (YYYY-MM-DD, 기본 오늘)')

    stage_recover_p = stage_sub.add_parser('recover', parents=[env_p],
                                           help='상세 수집이 실패한 작품의 링크를 다시 찾는다 (크롬 사용)')
    # 검색 후보를 내는 크롤러만 복구한다 (platforms.searchable). 목록은 등록부에서
    stage_recover_p.add_argument('--platform', choices=[p.enum for p in platforms.PLATFORMS])
    stage_recover_p.add_argument('--limit', type=int, default=50)

    stage_promote_p = stage_sub.add_parser('promote', parents=[env_p],
                                           help='검수 판정을 매핑으로 승격하고 목록을 본다 (Layer 3, 적재 때도 자동)')
    stage_promote_p.add_argument('--min', type=int, dest='min_count',
                                 help='승격할 최소 판정 횟수 (기본 REVIEW_PROMOTE_MIN, 없으면 3)')

    stage_queue_p = stage_sub.add_parser('queue', parents=[env_p],
                                         help='검수 대기 행을 바깥 검수에 넘길 JSON 으로 낸다 (enum 값 목록 포함)')
    stage_queue_p.add_argument('--run-id', dest='run_ids', action='append', help='런 (여러 번 줄 수 있음)')
    stage_queue_p.add_argument('--all', action='store_true', help='런과 상관없이 검수 대기 전체 (#72)')
    stage_queue_p.add_argument('--limit', type=int, help='[--all] 최대 행 수 (오래된 것부터)')
    stage_queue_p.add_argument('--output', required=True, help='저장할 JSON 파일')

    stage_suggest_p = stage_sub.add_parser('suggest', parents=[env_p],
                                           help='바깥 검수 제안을 검수 대기 행에 남긴다 (반영은 requests --resume)')
    stage_suggest_p.add_argument('--input', required=True,
                                 help='제안 JSON: [{"staging_id", "action": approve|reject|skip, "overrides", "confidence", "reason"}]')
    stage_suggest_p.add_argument('--by', default='claude', help='제안을 낸 도구 이름 (판정 기록에 suggest:<이름> 으로 남음)')

    stage_apply_p = stage_sub.add_parser('apply', parents=[env_p],
                                         help='바깥 검수 제안을 승인 · 거절로 반영 (--import 면 이번에 승인한 건만 BE 반영)')
    stage_apply_p.add_argument('--run-id', dest='run_ids', action='append', help='런 (여러 번 줄 수 있음)')
    stage_apply_p.add_argument('--all', action='store_true', help='런과 상관없이 제안이 붙은 검수 대기 전체')
    stage_apply_p.add_argument('--import', action='store_true', dest='do_import', help='이번에 승인한 건을 BE 로 보낸다')

    req_p = subparsers.add_parser('requests', help='노션 작품 추가 요청 자동 처리 (수집 → 검수 적재 → BE 반영 → 노션 표시)')
    req_p.add_argument('--env', choices=('dev', 'prod'), help='BE 대상 환경 (기본 STORIX_ENV, 없으면 dev)')
    req_p.add_argument('--min-id', type=int, dest='min_id', help='이 ID 부터 (기본 NOTION_REQUEST_MIN_ID, 없으면 441)')
    req_p.add_argument('--limit', type=int, help='이번에 처리할 최대 요청 수')
    req_mode = req_p.add_mutually_exclusive_group()
    req_mode.add_argument('--dry-run', action='store_true', dest='dry_run',
                          help='수집 · 검수 적재까지만. BE 반영 · 노션 표시는 하지 않고 이어받기 상태를 남긴다')
    req_mode.add_argument('--resume', action='store_true',
                          help='가장 최근 dry-run 을 이어받아 검수 제안 반영 → BE 반영 → 노션 표시 (다시 수집하지 않음)')
    req_p.add_argument('--state', help='[--resume] 이어받을 상태 파일 (기본: 아직 반영 안 한 가장 최근 것)')

    args = parser.parse_args()

    if args.command == 'login':
        cmd_login(args)
    elif args.command == 'crawl':
        from config import RUN_DIR
        from crawler import report
        report.reset()
        cmd_crawl(args)
        report.save(RUN_DIR, args.mode)
        if report.failed():
            sys.exit(1)  # 목록 0건 · 세션 만료 · 차단으로 멈춘 플랫폼이 있다 (#6)
    elif args.command == 'stage':
        cmd_stage(args)
    elif args.command == 'requests':
        cmd_requests(args)
    elif args.command == 'keepalive':
        cmd_keepalive(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
