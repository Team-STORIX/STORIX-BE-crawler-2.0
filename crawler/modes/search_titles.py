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
# '단행본'은 연재 사이트(comic/novel.naver.com)엔 없고 e북·단행본을 파는 곳에만 있으므로
# 시리즈·카카오·리디만 검색한다. 적재되는 works_type 은 크롤러가 상세에서 웹툰/웹소설로 판정한다.
_PLATFORM_TYPES = {
    'naver_webtoon': {'웹툰'},
    'naver_novel': {'웹소설'},
    'naver_series': {'웹툰', '웹소설', '단행본'},
    'kakao_page': {'웹툰', '웹소설', '단행본'},
    'ridibooks': {'웹툰', '웹소설', '단행본'},
}

# titles.txt 에서 인정하는 섹션 타입 (cli._TYPE_HEADERS 와 같은 값)
_VALID_TYPES = {t for types in _PLATFORM_TYPES.values() for t in types}


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
    # 원소는 제목이 아니라 (제목, 타입) — 섹션끼리는 서로 독립이라 제목만으로 묶으면
    # 웹툰판을 찾았을 때 웹소설 줄까지 지워진다.
    found: set[tuple[str, str | None]] = set()
    for p in targets:
        # 이 플랫폼이 취급하는 타입의 작품만 추린다. (타입 None은 항상 포함)
        accepted = _PLATFORM_TYPES.get(p, set())
        # 같은 제목이 여러 섹션에 있어도 한 플랫폼에서 두 번 검색하지는 않는다.
        # (시리즈·카카오·리디는 웹툰·웹소설·단행본을 다 받아서 같은 제목이 겹쳐 들어옴)
        # 대신 그 제목이 어느 섹션에서 왔는지 모아 둬, 파일 정리는 섹션별로 따로 한다.
        wanted: dict[str, set[str | None]] = {}
        for t, ttype in titles:
            if ttype is None or ttype in accepted:
                wanted.setdefault(t, set()).add(ttype)
        if not wanted:
            label = _CRAWLER_MAP[p][1]
            print(f'\n⏭️  [{label}] 해당 타입 작품이 없어 스킵')
            continue
        try:
            _search_and_write(wanted, p, found)
        except Exception as e:
            # 한 플랫폼(로그인 실패 등)의 오류가 나머지 플랫폼·파일 정리를 막지 않도록 격리
            print(f'⚠️  [{p}] 건너뜀: {e}')

    # 크롤 성공한 작품은 목록 파일에서 제거 → 파일엔 '못 찾은 작품'만 남는다.
    if titles_file:
        _remove_found_from_file(titles_file, found)


def _remove_found_from_file(titles_file: str, found: set[tuple[str, str | None]]) -> None:
    path = Path(titles_file)
    if not path.exists():
        return

    if not found:
        print(f'\nℹ️  찾은 작품이 없어 {path.name}을 그대로 둡니다.')
        return

    # 헤더(##)·주석(#)·빈 줄은 그대로 유지하고, 찾은 제목 줄만 제거한다.
    # 섹션을 따라가며 (제목, 타입) 단위로 지운다 — 같은 제목이 ## 웹툰 / ## 웹소설 에
    # 각각 있으면 서로 다른 작품이므로, 한쪽을 찾아도 다른 쪽 줄은 남긴다.
    lines = path.read_text(encoding='utf-8').splitlines()
    remaining: list[str] = []
    removed = 0
    current_type: str | None = None
    for ln in lines:
        stripped = ln.strip()
        if stripped.startswith('##'):
            header = stripped.lstrip('#').strip()
            # 모르는 헤더 아래 제목은 애초에 크롤되지 않아 found 에 없다 → None 으로 둬도 안전
            current_type = header if header in _VALID_TYPES else None
            remaining.append(ln)
            continue
        if stripped and not stripped.startswith('#') and (stripped, current_type) in found:
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


def _found_slots(
    title: str,
    types: set[str | None],
    record: dict,
) -> set[tuple[str, str | None]]:
    """검색 성공한 제목이 titles.txt 의 어느 섹션을 채운 것인지 판정.

    같은 제목이 ## 웹툰 / ## 웹소설 에 각각 있을 때, 상세에서 판정된 works_type 이
    요청 타입 중 하나면 그 섹션만 채운 것으로 본다. 판정이 안 되면(단행본 섹션 등)
    요청한 타입 전부를 채운 것으로 둔다.
    """
    t = title.strip()
    works_type = (record.get('works_type') or '').strip()
    if works_type and works_type in types:
        return {(t, works_type)}
    return {(t, ty) for ty in types}


def _search_and_write(
    wanted: dict[str, set[str | None]],
    platform: str,
    found: set[tuple[str, str | None]],
) -> set[tuple[str, str | None]]:
    """wanted(제목 → 그 제목이 나온 섹션 타입들)를 순회하며 검색·크롤·저장.
    성공한 (제목, 타입) 을 in-place 로 found 에 추가한다.

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
            print(f'\n🔍 [{label}] {len(wanted)}개 작품 검색 시작')

            for i, (title, types) in enumerate(wanted.items(), 1):
                print(f'\n  [{i}/{len(wanted)}] "{title}" 검색 중...')

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
                    found.update(_found_slots(title, types, result))
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
