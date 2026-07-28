import argparse
import sys
from pathlib import Path


def _configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            try:
                stream.reconfigure(encoding='utf-8', errors='replace')
            except Exception:
                pass


_configure_console()


# 섹션 헤더 텍스트 → 작품 타입
_TYPE_HEADERS = {'웹툰': '웹툰', '웹소설': '웹소설'}


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
        return [(t.strip(), None) for t in titles_str.split(',') if t.strip()]

    return []


def _parse_titles_file(path: Path) -> list[tuple[str, str | None]]:
    """섹션 헤더 형식 파싱.

        ## 웹툰            ← 이 아래 제목은 모두 '웹툰' 타입
        내가 키운 S급들
        ## 웹소설
        마법학교 마법사로 살아가는 법

    '#' 주석 줄과 빈 줄은 무시. 헤더 없이 나온 제목은 스킵한다.
    """
    result: list[tuple[str, str | None]] = []
    current_type: str | None = None

    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith('##'):
            header = line.lstrip('#').strip()
            current_type = _TYPE_HEADERS.get(header)
            if current_type is None:
                print(f'⚠️  알 수 없는 섹션 헤더 무시: "{line}" (웹툰 | 웹소설 만 지원)')
            continue
        if line.startswith('#'):
            continue  # 주석
        if current_type is None:
            print(f'⚠️  타입 헤더 없이 나온 제목 스킵: "{line}" (## 웹툰 / ## 웹소설 아래에 배치)')
            continue
        result.append((line, current_type))

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
            print('   지원 도메인: comic.naver.com | kakaopage.com | ridibooks.com(/books/<id> 단건 또는 카테고리)')
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

    args = parser.parse_args()

    if args.command == 'login':
        cmd_login(args)
    elif args.command == 'crawl':
        cmd_crawl(args)
    elif args.command == 'batch':
        cmd_batch(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
