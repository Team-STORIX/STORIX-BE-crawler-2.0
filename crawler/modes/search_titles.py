"""
search_titles 모드: 작품명 리스트 → 플랫폼 검색 → 크롤링 → JSONL 저장

이후 DB 적재는 기존 파이프라인 사용:
    python cli.py batch import --input ./output/YYYY-MM-DD/
"""
from modules.crawler.naver_crawler import NaverCrawler
from modules.crawler.kakao_crawler import KakaoCrawler
from crawler.output.jsonl_writer import JSONLWriter

SUPPORTED_PLATFORMS = ['naver_webtoon', 'kakao_page', 'all']


def run_search_titles(platform: str, titles: list[str]) -> None:
    if not titles:
        print('⚠️  검색할 작품명이 없습니다.')
        return

    if platform in ('naver_webtoon', 'all'):
        _search_and_write(titles, 'naver_webtoon')
    if platform in ('kakao_page', 'all'):
        _search_and_write(titles, 'kakao_page')


def _search_and_write(titles: list[str], platform: str) -> None:
    label = '네이버' if platform == 'naver_webtoon' else '카카오'
    CrawlerClass = NaverCrawler if platform == 'naver_webtoon' else KakaoCrawler

    crawler = CrawlerClass()
    crawler.start_driver()
    try:
        if not crawler.login():
            raise RuntimeError(f'{label} 로그인 실패')

        with JSONLWriter(platform, 'search_titles') as writer:
            ok_count = fail_count = skip_count = 0
            print(f'\n🔍 [{label}] {len(titles)}개 작품 검색 시작')

            for i, title in enumerate(titles, 1):
                print(f'\n  [{i}/{len(titles)}] "{title}" 검색 중...')

                url = crawler.search_url_by_title(title)
                if not url:
                    print(f'  ⚠️  검색 결과 없음 — 스킵')
                    skip_count += 1
                    continue

                result = crawler.crawl_detail_with_retry(url)
                if not result:
                    print(f'  ❌ 크롤링 실패')
                    fail_count += 1
                    continue

                writer.write(result)
                ok_count += 1

            print(
                f'\n✅ [{label}] 완료 — '
                f'수집 {ok_count}건 | 실패 {fail_count}건 | 스킵 {skip_count}건'
            )
            print(f'   DB 적재: python cli.py batch import --input {writer.path}')
    finally:
        crawler.close_driver()
