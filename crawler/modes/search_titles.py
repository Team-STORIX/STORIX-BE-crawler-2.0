"""
search_titles 모드: 작품명 리스트 → 플랫폼 검색 → 크롤링 → JSONL 저장

결과는 플랫폼마다 ./output/YYYY-MM-DD/<플랫폼>_search_titles.jsonl 에 쓴다.
이후 검수 적재 → BE 반영:
    python cli.py stage load --input './output/YYYY-MM-DD/*_search_titles.jsonl' --source search_titles
    python cli.py stage import --run-id <런>
"""
import threading
from pathlib import Path

from selenium.common.exceptions import InvalidSessionIdException, WebDriverException
from urllib3.exceptions import ReadTimeoutError as _DriverTimeoutError

from crawler import report
from modules.crawler.base_crawler import AuthExpiredError, RateLimitedError, SessionExpiredError, is_edition, pick_candidates
from modules.crawler.naver_crawler import NaverCrawler
from modules.crawler.naver_novel_crawler import NaverNovelCrawler
from modules.crawler.naver_series_crawler import NaverSeriesCrawler
from modules.crawler.kakao_crawler import KakaoCrawler
from modules.crawler.ridibooks_crawler import RidibooksCrawler
from modules.crawler.bomtoon_crawler import BomtoonCrawler
from crawler.output.jsonl_writer import JSONLWriter
from crawler.parallel import run_platforms

# 제목 검색(search_url_by_title) 단계에서 드라이버가 멈추면 나는 예외들.
# crawl_detail_with_retry 와 달리 이 단계는 재시작 보호가 없어 여기서 직접 처리한다.
_DRIVER_ERRORS = (
    InvalidSessionIdException,
    SessionExpiredError,
    _DriverTimeoutError,
    WebDriverException,
)

# bomtoon 은 성인 인증 세션이 필요해 all 에 넣지 않는다 (--platform bomtoon 으로 따로 돌린다)
SUPPORTED_PLATFORMS = ['naver_webtoon', 'naver_novel', 'naver_series', 'kakao_page', 'ridibooks', 'bomtoon', 'all']

_CRAWLER_MAP = {
    'naver_webtoon': (NaverCrawler, '네이버 웹툰'),
    'naver_novel': (NaverNovelCrawler, '네이버 웹소설'),
    'naver_series': (NaverSeriesCrawler, '네이버 시리즈'),
    'kakao_page': (KakaoCrawler, '카카오'),
    'ridibooks': (RidibooksCrawler, '리디북스'),
    'bomtoon': (BomtoonCrawler, '봄툰'),
}

_ALL_TARGETS = ['naver_webtoon', 'naver_novel', 'naver_series', 'kakao_page', 'ridibooks']

# 플랫폼이 취급하는 작품 유형. 여기 없는 유형의 작품은 해당 플랫폼에서 검색하지 않는다.
# (유형 None = 인라인 입력 → 유형 무관하게 전 플랫폼 검색)
# 적재되는 works_type 은 크롤러가 상세에서 웹툰/웹소설로 판정한다.
_PLATFORM_TYPES = {
    'naver_webtoon': {'웹툰'},
    'naver_novel': {'웹소설'},
    'naver_series': {'웹툰', '웹소설'},
    'kakao_page': {'웹툰', '웹소설'},
    'ridibooks': {'웹툰', '웹소설'},
    'bomtoon': {'웹툰', '웹소설'},
}

# 목록 파일 섹션 → 찾을 유형. '전체' 는 웹툰 · 웹소설을 각각 찾아 있는 판을 다 수집한다
# (단행본 섹션은 없앴다 — 웹소설 연재판이 없으면 e북을 대신 고르는 규칙이 있다. cli._TYPE_HEADERS)
SECTION_TYPES = {'웹툰': {'웹툰'}, '웹소설': {'웹소설'}, '전체': {'웹툰', '웹소설'}}

# titles.txt 에서 인정하는 섹션 (cli._TYPE_HEADERS 의 값)
_VALID_TYPES = set(SECTION_TYPES)


def run_search_titles(
    platform: str,
    titles: list[tuple[str, str | None]],
    titles_file: str = None,
    parallel: int = 1,
    mode: str = 'search_titles',
    on_found=None,
) -> None:
    """mode 는 출력 파일 이름(<플랫폼>_<mode>.jsonl). on_found(제목, 수집 결과)는 작품을 저장할 때마다 불린다
    (노션 요청 처리가 요청 ↔ 수집 결과를 잇는 데 쓴다, #66)."""
    if not titles:
        print('⚠️  검색할 작품명이 없습니다.')
        return

    targets = _ALL_TARGETS if platform == 'all' else [platform]
    # found 를 호출자가 소유해, 한 플랫폼이 중간에 터져도 그때까지 크롤한 진행분이
    # titles.txt 정리에 반영되도록 한다. (_search_and_write 가 이 집합을 in-place 로 채움)
    # 원소는 제목이 아니라 (제목, 타입) — 섹션끼리는 서로 독립이라 제목만으로 묶으면
    # 웹툰판을 찾았을 때 웹소설 줄까지 지워진다.
    found: set[tuple[str, str | None]] = set()

    def run(p: str) -> None:
        # 이 플랫폼이 취급하는 타입의 작품만 추린다. (타입 None은 항상 포함)
        accepted = _PLATFORM_TYPES.get(p, set())
        # 같은 제목이 여러 섹션에 있어도 한 플랫폼에서 두 번 검색하지는 않는다.
        # (시리즈·카카오·리디는 웹툰·웹소설·단행본을 다 받아서 같은 제목이 겹쳐 들어옴)
        # 대신 그 제목이 어느 섹션에서 왔는지 모아 둬, 파일 정리는 섹션별로 따로 한다.
        wanted: dict[str, set[str | None]] = {}
        for t, ttype in titles:
            if ttype is None or SECTION_TYPES.get(ttype, set()) & accepted:
                wanted.setdefault(t, set()).add(ttype)
        if not wanted:
            label = _CRAWLER_MAP[p][1]
            print(f'\n⏭️  [{label}] 해당 타입 작품이 없어 스킵')
            return
        _search_and_write(wanted, p, found, mode, on_found)

    # 한 플랫폼(로그인 실패 등)의 오류는 나머지 플랫폼 · 파일 정리를 막지 않는다 (run_platforms 가 격리)
    run_platforms(targets, run, parallel)

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
            header = '웹소설' if header == '단행본' else header  # 옛 단행본 섹션은 웹소설로 읽었다
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
    요청 섹션 중 하나면 그 섹션만 채운 것으로 본다. ## 전체 는 어느 판이든 하나 찾으면 채운 것이다.
    판정이 안 되면 요청한 섹션 전부를 채운 것으로 둔다.
    """
    t = title.strip()
    works_type = (record.get('works_type') or '').strip()
    slots = set()
    if works_type and works_type in types:
        slots.add((t, works_type))
    if '전체' in types and works_type in SECTION_TYPES['전체']:
        slots.add((t, '전체'))
    return slots or {(t, ty) for ty in types}


def _crawl_types(sections: set[str | None], platform: str) -> list[str | None]:
    """목록 섹션 → 이 플랫폼에서 찾을 유형 (전체 → 웹툰 · 웹소설). 플랫폼이 안 다루는 유형은 뺀다."""
    types: set[str | None] = {None} if None in sections else set()
    for section in sections - {None}:
        types |= SECTION_TYPES[section] & _PLATFORM_TYPES[platform]
    return sorted(types, key=lambda t: t or '')


# 원하는 유형의 작품을 찾기까지 상세를 열어 볼 후보 수
MAX_TYPE_TRIES = 3

# 플랫폼을 동시에 돌리면 여러 스레드가 found 를 채운다
_found_lock = threading.Lock()


def _crawl_for_type(crawler, candidates: list[dict], ttype: str | None, crawled: dict,
                    query: str = '') -> list[dict]:
    """원하는 유형(웹툰 · 웹소설 · 단행본 · None)의 본편과 판본(19세 완전판 · 개정판)을 수집해 돌려준다.

    검색 화면의 유형 힌트로 먼저 거르고(pick_candidates), 상세에서 판정한 works_type 이
    원하는 유형과 다르면 다음 후보를 연다. 단행본 · 유형 미지정은 상세 유형을 따지지 않는다.
    판본은 BE 가 다른 작품으로 판정하므로 본편과 따로 담는다. 외전은 후보 단계에서 이미 빠져 있다.
    """
    want = ttype if ttype in ('웹툰', '웹소설') else None
    picked = pick_candidates(candidates, ttype)
    if not is_edition(query):
        mains = [c for c in picked if not is_edition(c['text'])]
        editions = [c for c in picked if is_edition(c['text'])]
    else:
        mains, editions = picked, []

    def open_detail(c):
        if c['url'] not in crawled:
            print(f"   ↳ 후보 ({c['kind']}{', ' + c['type_hint'] if c['type_hint'] else ''}): {c['url']} [{c['text']}]")
            crawled[c['url']] = crawler.crawl_detail_with_retry(c['url'])
        result = crawled[c['url']]
        if not result:
            return None
        got = (result.get('works_type') or '').strip()
        if want and got != want:
            print(f'   ↳ {got or "유형 없음"} 이라 {want} 아님 — 다음 후보')
            return None
        return result

    results = []
    for c in mains[:MAX_TYPE_TRIES]:
        r = open_detail(c)
        if r:
            if ttype == '웹소설' and c['type_hint'] == '단행본' and r.get('age_classification'):
                # 연재판이 없어 e북으로 대신했다. e북 연령(권마다 다름, 예: 마도조사 4권 성인)이
                # 같은 작품 웹소설 연령을 덮어쓰지 않게 비운다. 연령은 연재판이 있는 플랫폼에서 정한다
                print(f"   ↳ e북으로 대신 — 연령({r['age_classification']})은 보내지 않음")
                r = {**r, 'age_classification': ''}
            results.append(r)
            break
    for c in editions[:MAX_TYPE_TRIES]:
        r = open_detail(c)
        if r:
            results.append(r)
    if not results:
        print(f'  ⚠️  {ttype or "작품"} 후보 없음 — 스킵')
    return results


def _search_and_write(
    wanted: dict[str, set[str | None]],
    platform: str,
    found: set[tuple[str, str | None]],
    mode: str = 'search_titles',
    on_found=None,
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
            raise AuthExpiredError(f'{label} 로그인 실패')

        with JSONLWriter(platform, mode) as writer:
            ok_count = fail_count = skip_count = 0
            print(f'\n🔍 [{label}] {len(wanted)}개 작품 검색 시작')

            for i, (title, types) in enumerate(wanted.items(), 1):
                print(f'\n  [{i}/{len(wanted)}] "{title}" 검색 중...')

                try:
                    candidates = crawler.search_candidates(title)
                    if not candidates:
                        print(f'  ⚠️  검색 결과 없음 — 스킵')
                        report.record(crawler.REPORT_PLATFORM, report.SEARCH_FAILED, title)
                        skip_count += 1
                        continue

                    crawled: dict[str, dict | None] = {}  # 같은 후보를 유형마다 다시 열지 않는다
                    written: set[str] = set()  # 다른 유형 섹션에서 이미 저장한 같은 작품은 다시 안 쓴다
                    for ttype in _crawl_types(types, platform):
                        results = _crawl_for_type(crawler, candidates, ttype, crawled, title)
                        if not results:
                            skip_count += 1
                        for result in results:
                            if result['source_url'] in written:
                                continue
                            written.add(result['source_url'])
                            writer.write(result)
                            with _found_lock:
                                found.update(_found_slots(title, types, result))
                            if on_found:
                                on_found(title, result)
                            ok_count += 1
                except (RateLimitedError, AuthExpiredError):
                    # 차단이 계속되거나 재로그인이 실패했다 — 이 플랫폼은 여기서 멈춘다 (리포트는 crawl_detail_with_retry 가 남김)
                    raise
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
            print(f'   검수 적재: python cli.py stage load --input {writer.path} --source search_titles')
    finally:
        crawler.close_driver()

    return found
