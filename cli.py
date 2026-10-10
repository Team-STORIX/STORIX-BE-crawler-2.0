import argparse
import sys
from pathlib import Path

import config  # noqa: F401  (stdout/stderr UTF-8 재설정 + .env 로드)


# 섹션 헤더 텍스트 → 작품 타입
# 이 타입은 DB 의 works_type 이 아니라 '어느 플랫폼에서 검색할지' 고르는 필터로만 쓰인다
# (search_titles._PLATFORM_TYPES). 적재되는 works_type 은 크롤러가 상세페이지에서 다시 판정한다.
_TYPE_HEADERS = {'웹툰': '웹툰', '웹소설': '웹소설', '단행본': '단행본'}


def _load_titles(args) -> list[tuple[str, str | None]]:
    """--titles-file(섹션 헤더 형식) 또는 --titles에서 (제목, 타입) 목록을 읽어 반환.

    타입은 '웹툰' | '웹소설' | None(인라인 입력 → 타입 미지정).
    """
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
        ## 단행본          ← 연재처가 없는 단행본/e북 (시리즈·카카오·리디만 검색)
        패밀리 레스토랑 가자

    '#' 주석 줄과 빈 줄은 무시. 헤더 없이 나온 제목은 스킵한다.
    같은 섹션에 같은 제목이 여러 번 있으면 첫 줄만 쓴다(같은 작품을 두 번 크롤하지 않도록).
    다른 섹션에 같은 제목이 있는 건 별개 레코드일 수 있으므로 그대로 둔다.
    """
    result: list[tuple[str, str | None]] = []
    seen: set[tuple[str, str]] = set()
    dupes = 0
    current_type: str | None = None

    # utf-8-sig: 메모장 등으로 편집해 BOM 이 붙어도 첫 줄이 깨지지 않게
    for raw in path.read_text(encoding='utf-8-sig').splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith('##'):
            header = line.lstrip('#').strip()
            current_type = _TYPE_HEADERS.get(header)
            if current_type is None:
                print(f'⚠️  알 수 없는 섹션 헤더 무시: "{line}" '
                      f'({" | ".join(_TYPE_HEADERS)} 만 지원)')
            continue
        if line.startswith('#'):
            continue  # 주석
        if current_type is None:
            print(f'⚠️  타입 헤더 없이 나온 제목 스킵: "{line}" '
                  f'({" / ".join("## " + h for h in _TYPE_HEADERS)} 아래에 배치)')
            continue
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
    from config import NAVER_COOKIE_FILE, KAKAO_COOKIE_FILE, RIDIBOOKS_COOKIE_FILE

    cookie_files = {
        'naver_webtoon': NAVER_COOKIE_FILE,
        'kakao_page': KAKAO_COOKIE_FILE,
        'ridibooks': RIDIBOOKS_COOKIE_FILE,
    }

    platform = args.platform
    targets = ['naver_webtoon', 'kakao_page', 'ridibooks'] if platform == 'all' else [platform]

    for p in targets:
        print(f'\n{"="*60}')
        print(f'🔐 [{p}] 로그인 세션 저장')
        print(f'{"="*60}')

        # 기존 세션 초기화
        cookie_file = cookie_files.get(p)
        if cookie_file and cookie_file.exists():
            cookie_file.unlink()
            print(f'🗑️  기존 세션 파일 삭제: {cookie_file.name}')

        if p == 'naver_webtoon':
            from modules.crawler.naver_crawler import NaverCrawler
            crawler = NaverCrawler()
        elif p == 'kakao_page':
            from modules.crawler.kakao_crawler import KakaoCrawler
            crawler = KakaoCrawler()
        elif p == 'ridibooks':
            from modules.crawler.ridibooks_crawler import RidibooksCrawler
            crawler = RidibooksCrawler()
        else:
            print(f'❌ 지원하지 않는 플랫폼: {p}')
            continue

        crawler.start_driver()
        try:
            if crawler.login():
                print(f'✅ [{p}] 로그인 성공. 쿠키가 sessions/ 에 저장되었습니다.')
            else:
                print(f'❌ [{p}] 로그인 실패.')
        finally:
            crawler.close_driver()


def cmd_crawl(args):
    mode = args.mode
    platform = args.platform

    # custom_url 모드는 platform 불필요
    if mode != 'custom_url' and not platform:
        print('❌ custom_url 제외한 모드는 --platform이 필수입니다.')
        sys.exit(1)

    if mode == 'initial':
        from crawler.modes.initial import run_initial, SUPPORTED_PLATFORMS
        if platform != 'all' and platform not in SUPPORTED_PLATFORMS:
            print(f'❌ 지원하지 않는 플랫폼: {platform}')
            print(f'   지원 목록: {", ".join(SUPPORTED_PLATFORMS)} | all')
            sys.exit(1)
        run_initial(platform)

    elif mode == 'new_works':
        from crawler.modes.new_works import run_new_works, SUPPORTED_PLATFORMS
        if platform != 'all' and platform not in SUPPORTED_PLATFORMS:
            print(f'❌ 지원하지 않는 플랫폼: {platform}')
            print(f'   지원 목록: {", ".join(SUPPORTED_PLATFORMS)} | all')
            sys.exit(1)
        run_new_works(platform)

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
        run_update_fields(platform, args.input)

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
        run_search_titles(platform, titles, titles_file=getattr(args, 'titles_file', None))

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


def cmd_batch(args):
    sub = getattr(args, 'batch_command', None)
    if sub == 'import':
        from batch.importer import run_import
        watch = getattr(args, 'watch', False)
        verbose = getattr(args, 'verbose', False)
        run_import(args.input, watch=watch, verbose=verbose)
    elif sub == 'review':
        _cmd_review(args.input)
    elif sub == 'fix':
        from batch.reviewer import run_review_fix
        input_path = getattr(args, 'input', None)
        run_review_fix(input_path)
    else:
        print('❌ batch 서브 명령이 없습니다. (import | review | fix)')
        sys.exit(1)


def _cmd_review(input_path: str):
    import json
    from pathlib import Path

    path = Path(input_path)
    if not path.exists():
        print(f'❌ 파일을 찾을 수 없습니다: {path}')
        sys.exit(1)

    total = 0
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += 1
            record = entry.get('record', {})
            reasons = entry.get('reason', [])
            queued_at = entry.get('queued_at', '')
            print(f'[{total}] {queued_at} | {record.get("works_name", "(제목없음)")}')
            for r in reasons:
                print(f'     ↳ {r}')

    print(f'\n총 {total}건의 수동 검수 항목이 있습니다.')


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
    if sub not in ('load', 'import', 'stats'):
        print('❌ stage 서브 명령이 없습니다. (load | import | stats)')
        sys.exit(1)

    from review.store import STAGING_DATABASE
    print(f'🎯 대상 {target.upper()} — BE {storix_env.api_base_url(target)} / staging DB {STAGING_DATABASE}')
    ensure_schema()
    conn = connect()
    try:
        store = StagingStore(conn)
        if sub == 'load':
            from review.service import load_run, read_jsonl
            if args.catalog_file:
                os.environ['REVIEW_CATALOG_FILE'] = args.catalog_file
            try:
                catalog = load_catalog()
            except (BackendAuthError, OSError) as e:
                # OSError: BE 연결 실패(URLError) 포함
                print(f'❌ enum 카탈로그를 못 받았습니다: {e}')
                sys.exit(1)
            path = Path(args.input)
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
        else:
            print(json.dumps(store.stats(), ensure_ascii=False, indent=2))
    finally:
        conn.close()


def main():
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
    crawl_p.add_argument('--count',
                         type=int,
                         help='[custom_url 전용] 크롤링 개수 (기본값: 500개, 무한 스크롤 무시)')

    login_p = subparsers.add_parser('login', help='로그인 세션 저장 (Docker 실행 전 선행)')
    login_p.add_argument('--platform', required=True,
                         help='naver_webtoon | kakao_page | ridibooks | all')

    batch_p = subparsers.add_parser('batch', help='JSONL → DB 적재')
    batch_sub = batch_p.add_subparsers(dest='batch_command')

    import_p = batch_sub.add_parser('import', help='JSONL 파일 DB 적재')
    import_p.add_argument('--input', required=False, help='적재할 디렉토리 또는 파일 경로 (생략 시 당일 폴더 자동 감지)')
    import_p.add_argument('--watch', action='store_true', help='output 폴더 감시 모드')
    import_p.add_argument('--verbose', action='store_true', help='검수큐 행의 검증 오류를 실시간 출력')

    review_p = batch_sub.add_parser('review', help='수동 검수 큐 조회')
    review_p.add_argument('--input', required=True, help='manual_review_queue.jsonl 경로')

    fix_p = batch_sub.add_parser('fix', help='검수 큐 대화형 수정')
    fix_p.add_argument('--input', required=False, help='manual_review_queue.jsonl 경로 (생략 시 당일 폴더 자동 감지)')

    stage_p = subparsers.add_parser('stage', help='검수 파이프라인 (works_staging → BE import API)')
    stage_sub = stage_p.add_subparsers(dest='stage_command')

    # 모든 stage 명령에 붙는다. 생략하면 STORIX_ENV(기본 dev)
    env_p = argparse.ArgumentParser(add_help=False)
    env_p.add_argument('--env', choices=('dev', 'prod'), help='BE 대상 환경 (기본 STORIX_ENV, 없으면 dev)')

    stage_load_p = stage_sub.add_parser('load', parents=[env_p], help='JSONL 을 staging 에 적재하고 Layer 1 · 1.5 판정')
    stage_load_p.add_argument('--input', required=True, help='JSONL 파일 또는 디렉토리')
    stage_load_p.add_argument('--source', required=True,
                              help='직전 런과 건수를 비교할 단위 (예: naver_webtoon_initial)')
    stage_load_p.add_argument('--catalog-file', dest='catalog_file',
                              help='BE 대신 저장해 둔 enum 카탈로그 JSON 사용')

    stage_import_p = stage_sub.add_parser('import', parents=[env_p], help='AUTO_PASS · APPROVED 를 BE 로 승격')
    stage_import_p.add_argument('--limit', type=int, default=500)
    stage_import_p.add_argument('--run-id', dest='run_id', help='이 런만 보낸다 (생략하면 보류 안 된 전체 런)')
    stage_import_p.add_argument('--yes', action='store_true', help='확인 없이 진행 (건수를 이미 확인한 경우)')

    stage_sub.add_parser('stats', parents=[env_p], help='상태별 건수 · 자동화율')

    args = parser.parse_args()

    if args.command == 'login':
        cmd_login(args)
    elif args.command == 'crawl':
        cmd_crawl(args)
    elif args.command == 'batch':
        cmd_batch(args)
    elif args.command == 'stage':
        cmd_stage(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
