"""
search_titles 모드: 작품명 리스트 → 플랫폼 검색 → 크롤링 → JSONL 저장

이후 DB 적재는 기존 파이프라인 사용:
    python cli.py batch import --input ./output/YYYY-MM-DD/
"""
from pathlib import Path

from selenium.common.exceptions import InvalidSessionIdException, WebDriverException
from urllib3.exceptions import ReadTimeoutError as _DriverTimeoutError

from modules.crawler.base_crawler import SessionExpiredError
from modules.crawler.naver_crawler import NaverCrawler
from modules.crawler.naver_novel_crawler import NaverNovelCrawler
from modules.crawler.naver_series_crawler import NaverSeriesCrawler
from modules.crawler.kakao_crawler import KakaoCrawler
from modules.crawler.ridibooks_crawler import RidibooksCrawler
from crawler.output.jsonl_writer import JSONLWriter

# 제목 검색(search_url_by_title) 단계에서 드라이버가 멈추면 나는 예외들.
# crawl_detail_with_retry 와 달리 이 단계는 재시작 보호가 없어 여기서 직접 처리한다.
_DRIVER_ERRORS = (
    InvalidSessionIdException,
    SessionExpiredError,
    _DriverTimeoutError,
    WebDriverException,
)

SUPPORTED_PLATFORMS = ['naver_webtoon', 'naver_novel', 'naver_series', 'kakao_page', 'ridibooks', 'all']

_CRAWLER_MAP = {
    'naver_webtoon': (NaverCrawler, '네이버 웹툰'),
    'naver_novel': (NaverNovelCrawler, '네이버 웹소설'),
    'naver_series': (NaverSeriesCrawler, '네이버 시리즈'),
    'kakao_page': (KakaoCrawler, '카카오'),
    'ridibooks': (RidibooksCrawler, '리디북스'),
}

_ALL_TARGETS = ['naver_webtoon', 'naver_novel', 'naver_series', 'kakao_page', 'ridibooks']

# 플랫폼이 취급하는 작품 타입. 여기 없는 타입의 작품은 해당 플랫폼에서 검색하지 않는다.
# (타입 None = 인라인 입력 → 타입 무관하게 전 플랫폼 검색)
_PLATFORM_TYPES = {
    'naver_webtoon': {'웹툰'},
    'naver_novel': {'웹소설'},
    'naver_series': {'웹툰', '웹소설'},
    'kakao_page': {'웹툰', '웹소설'},
    'ridibooks': {'웹툰', '웹소설'},
}


def run_search_titles(
    platform: str,
    titles: list[tuple[str, str | None]],
    titles_file: str = None,
) -> None:
    if not titles:
        print('⚠️  검색할 작품명이 없습니다.')
        return

    targets = _ALL_TARGETS if platform == 'all' else [platform]
    # found 를 호출자가 소유해, 한 플랫폼이 중간에 터져도 그때까지 크롤한 진행분이
    # titles.txt 정리에 반영되도록 한다. (_search_and_write 가 이 집합을 in-place 로 채움)
    found: set[str] = set()
    for p in targets:
        # 이 플랫폼이 취급하는 타입의 작품만 추린다. (타입 None은 항상 포함)
        accepted = _PLATFORM_TYPES.get(p, set())
        subset = [t for (t, ttype) in titles if ttype is None or ttype in accepted]
        if not subset:
            label = _CRAWLER_MAP[p][1]
            print(f'\n⏭️  [{label}] 해당 타입 작품이 없어 스킵')
            continue
        try:
            _search_and_write(subset, p, found)
        except Exception as e:
            # 한 플랫폼(로그인 실패 등)의 오류가 나머지 플랫폼·파일 정리를 막지 않도록 격리
            print(f'⚠️  [{p}] 건너뜀: {e}')

    # 크롤 성공한 작품은 목록 파일에서 제거 → 파일엔 '못 찾은 작품'만 남는다.
    if titles_file:
        _remove_found_from_file(titles_file, found)


def _remove_found_from_file(titles_file: str, found: set[str]) -> None:
    path = Path(titles_file)
    if not path.exists():
        return

    if not found:
        print(f'\nℹ️  찾은 작품이 없어 {path.name}을 그대로 둡니다.')
        return

    # 헤더(##)·주석(#)·빈 줄은 그대로 유지하고, 찾은 제목 줄만 제거한다.
    lines = path.read_text(encoding='utf-8').splitlines()
    remaining: list[str] = []
    removed = 0
    for ln in lines:
        stripped = ln.strip()
        if stripped and not stripped.startswith('#') and stripped in found:
            removed += 1
            continue
        remaining.append(ln)

    leftover_titles = [
        ln.strip() for ln in remaining
        if ln.strip() and not ln.strip().startswith('#')
    ]

    path.write_text(
        ('\n'.join(remaining) + '\n') if remaining else '',
        encoding='utf-8',
    )
    print(f'\n🧹 {path.name} 갱신 — 찾음 {removed}개 제거, 남은 작품 {len(leftover_titles)}개')
    if leftover_titles:
        print('   남은 작품: ' + ', '.join(leftover_titles))


def _search_and_write(titles: list[str], platform: str, found: set[str]) -> set[str]:
    """titles 를 순회하며 검색·크롤·저장. 성공 제목을 in-place 로 found 에 추가한다.

    한 작품에서 드라이버가 멈추거나(타임아웃) 예외가 나도 그 작품만 건너뛰고
    나머지를 계속한다. 드라이버 이상이면 재시작 후 이어서 진행한다.
    """
    CrawlerClass, label = _CRAWLER_MAP[platform]

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

                try:
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
                    found.add(title.strip())
                    ok_count += 1
                except _DRIVER_ERRORS as e:
                    # 제목 검색 단계의 드라이버 다운. 재시작 후 다음 작품 계속.
                    print(f'  ♻️  드라이버 이상 — 재시작 후 계속: {e}')
                    crawler._restart_driver()
                    fail_count += 1
                    continue
                except Exception as e:
                    # 그 외 예기치 못한 오류도 이 작품만 스킵.
                    print(f'  ❌ 예기치 못한 오류 — 스킵: {e}')
                    fail_count += 1
                    continue

            print(
                f'\n✅ [{label}] 완료 — '
                f'수집 {ok_count}건 | 실패 {fail_count}건 | 스킵 {skip_count}건'
            )
            print(f'   DB 적재: python cli.py batch import --input {writer.path}')
    finally:
        crawler.close_driver()

    return found
