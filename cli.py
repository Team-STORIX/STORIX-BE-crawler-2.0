import argparse
import sys


def cmd_crawl(args):
    mode = args.mode
    platform = args.platform

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
        from crawler.modes.update_fields import run_update_fields
        if not args.input:
            print('❌ update_fields 모드는 --input 경로가 필요합니다.')
            print('   예: python cli.py crawl --platform all --mode update_fields --input ./output/2026-05-09/')
            sys.exit(1)
        run_update_fields(platform, args.input)

    else:
        print(f'❌ 알 수 없는 모드: {mode}')
        sys.exit(1)


def cmd_batch(args):
    sub = getattr(args, 'batch_command', None)
    if sub == 'import':
        from batch.importer import run_import
        watch = getattr(args, 'watch', False)
        run_import(args.input, watch=watch)
    elif sub == 'review':
        _cmd_review(args.input)
    else:
        print('❌ batch 서브 명령이 없습니다. (import | review)')
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
    crawl_p.add_argument('--platform', required=True,
                         help='naver_webtoon | kakao_page | all')
    crawl_p.add_argument('--mode', required=True,
                         choices=['initial', 'new_works', 'update_fields'])
    crawl_p.add_argument('--input',
                         help='[update_fields 전용] 기존 JSONL 파일 또는 디렉토리 경로')

    batch_p = subparsers.add_parser('batch', help='JSONL → DB 적재')
    batch_sub = batch_p.add_subparsers(dest='batch_command')

    import_p = batch_sub.add_parser('import', help='JSONL 파일 DB 적재')
    import_p.add_argument('--input', required=True, help='적재할 디렉토리 또는 파일 경로')
    import_p.add_argument('--watch', action='store_true', help='output 폴더 감시 모드')

    review_p = batch_sub.add_parser('review', help='수동 검수 큐 조회')
    review_p.add_argument('--input', required=True, help='manual_review_queue.jsonl 경로')

    args = parser.parse_args()

    if args.command == 'crawl':
        cmd_crawl(args)
    elif args.command == 'batch':
        cmd_batch(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
