import re
from urllib.parse import urlparse, parse_qs, urlsplit, urlunsplit
from modules.crawler.naver_crawler import NaverCrawler
from modules.crawler.naver_series_crawler import NaverSeriesCrawler
from modules.crawler.kakao_crawler import KakaoCrawler
from modules.crawler.ridibooks_crawler import RidibooksCrawler
from crawler.output.jsonl_writer import JSONLWriter

SUPPORTED_PLATFORMS = ['naver_webtoon', 'naver_series', 'kakao_page', 'ridibooks']

# 리디북스 단건 상세 URL 판별: /books/<숫자>
_RIDI_BOOK_RE = re.compile(r'/books/(\d+)')

# 카카오페이지 단건 상세 URL 판별: /content/<숫자>
_KAKAO_CONTENT_RE = re.compile(r'/content/(\d+)')

# 네이버 시리즈 모바일 → PC. 상세 파서가 PC 레이아웃 셀렉터(.end_head 등) 전용이라
# m.series 로 들어오면 로딩 타임아웃이 난다.
_SERIES_MOBILE_RE = re.compile(r'^(https?://)m\.series\.naver\.com')

# 네이버 웹툰 단건 상세 URL 판별: ?titleId=<숫자>
_NAVER_TITLE_ID_RE = re.compile(r'titleId=(\d+)')


def _to_naver_work_url(url: str) -> str:
    """네이버 웹툰 회차 URL → 작품 URL. (`/detail?titleId=..&no=5` → `/list?titleId=..`)

    상세 파서가 작품 페이지 레이아웃 전용이라 회차 페이지를 그대로 열면 제목을 못 읽는다.
    week 같은 잔여 쿼리도 같이 털어낸다. 정식/도전/베스트도전 경로는 그대로 유지.
    """
    sp = urlsplit(url)
    m = _NAVER_TITLE_ID_RE.search(sp.query)
    if not m:
        return url
    path = re.sub(r'/detail/?$', '/list', sp.path)
    return urlunsplit((sp.scheme, sp.netloc, path, f'titleId={m.group(1)}', ''))


def parse_url_to_platform(url: str) -> tuple[str, str]:
    """
    URL을 파싱해서 (platform, page_type) 반환.

    Returns:
        (platform, page_type): 예) ('naver_webtoon', 'dailyPlus'),
                               시리즈 단건은 ('naver_series', 'detail'),
                               리디 단건은 ('ridibooks', 'book'),
                               리디 카테고리는 ('ridibooks', 'category')

    Raises:
        ValueError: 지원하지 않는 URL
    """
    parsed = urlparse(url)
    domain = parsed.netloc.lower()

    # 네이버 시리즈 — 아래 'naver.com' 분기가 모든 네이버 도메인을 웹툰으로
    # 삼켜버리므로 반드시 먼저 걸러야 한다. (m.series 포함)
    if 'series.naver.com' in domain:
        return 'naver_series', ('detail' if 'detail.series' in parsed.path else 'list')

    # 네이버 웹툰: ?titleId=<id> 단건 상세 vs 요일/완결 등 목록 탭
    if 'naver.com' in domain:
        params = parse_qs(parsed.query)
        if params.get('titleId'):
            return 'naver_webtoon', 'detail'
        tab = params.get('tab', [''])[0] or 'dailyPlus'
        return 'naver_webtoon', tab

    # 카카오페이지: /content/<id> 단건 상세 vs 목록(메뉴·랭킹) 페이지
    # 실제 서비스 도메인은 page.kakao.com. kakaopage.com 은 구 도메인이라 같이 받는다.
    elif 'page.kakao.com' in domain or 'kakaopage.com' in domain:
        return 'kakao_page', ('content' if _KAKAO_CONTENT_RE.search(parsed.path) else 'list')

    # 리디북스: /books/<id> 단건 상세 vs 카테고리(베스트셀러) 목록
    elif 'ridibooks.com' in domain:
        return 'ridibooks', ('book' if _RIDI_BOOK_RE.search(parsed.path) else 'category')

    else:
        raise ValueError(f'지원하지 않는 도메인: {domain}')


def run_custom_url(url: str, count: int | None = None) -> None:
    """
    URL 기반 커스텀 크롤링.
    
    Args:
        url: 크롤링할 URL
        count: 크롤링 개수 (None이면 무제한)
    """
    print(f'\n{"="*70}')
    print(f'🔗 URL 크롤링 시작')
    print(f'{"="*70}')
    print(f'URL: {url}')
    if count:
        print(f'개수 제한: {count}개')
    
    try:
        platform, page_type = parse_url_to_platform(url)
        print(f'✅ 감지: {platform} | {page_type}')
    except ValueError as e:
        print(f'❌ {e}')
        return
    
    if platform == 'naver_webtoon':
        _crawl_naver(url, page_type, count)
    elif platform == 'naver_series':
        _crawl_naver_series(url, page_type)
    elif platform == 'kakao_page':
        _crawl_kakao(url, page_type, count)
    elif platform == 'ridibooks':
        _crawl_ridibooks(url, page_type, count)


def _crawl_naver(url: str, tab: str, count: int | None) -> None:
    """네이버 웹툰 커스텀 크롤링.

    tab == 'detail' → ?titleId=<id> 단건 상세 1건 크롤 (회차 URL도 작품 URL로 변환)
    그 외           → 해당 탭 목록에서 작품 URL 수집 후 각 상세 크롤
    """
    crawler = NaverCrawler()

    print(f'\n🔐 네이버 로그인...')
    crawler.start_driver()

    try:
        if not crawler.login():
            print('❌ 로그인 실패')
            return

        print(f'✅ 로그인 성공')

        if tab == 'detail':
            work_url = _to_naver_work_url(url)
            if work_url != url:
                print(f'ℹ️  회차 URL → 작품 URL 변환: {work_url}')
            work_urls = [work_url]
            print(f'\n📡 단건 상세 크롤링...')
        else:
            print(f'\n📡 {tab} 탭에서 크롤링...')
            work_urls = [
                w['source_url']
                for w in _fetch_naver_works(crawler, url, tab, count)
                if w.get('source_url')
            ]

        if not work_urls:
            print('❌ 크롤링할 URL이 없습니다.')
            return

        print(f'\n📝 JSONL 저장 중... ({len(work_urls)}건)')

        with JSONLWriter(platform='naver_webtoon', mode='custom_url') as writer:
            for w_url in work_urls:
                detail = crawler.crawl_detail_with_retry(w_url)
                if detail:
                    writer.write(detail)

        print(f'✅ 완료: {writer.count}건 저장')


    finally:
        crawler.close_driver()


def _crawl_naver_series(url: str, page_type: str) -> None:
    """네이버 시리즈 단건 상세 크롤링.

    목록 페이지는 지원하지 않는다 — 랭킹·카테고리마다 DOM이 달라 목록 수집기를
    따로 둬야 하는데, 이 모드가 필요한 건 검색이 놓친 작품의 단건 보정이기 때문.
    """
    if page_type != 'detail':
        print('❌ 네이버 시리즈는 상세 URL만 지원합니다.')
        print('   예: https://series.naver.com/novel/detail.series?productNo=14490883')
        return

    pc_url = _SERIES_MOBILE_RE.sub(r'\1series.naver.com', url)
    if pc_url != url:
        print(f'ℹ️  모바일 URL → PC URL 변환: {pc_url}')

    crawler = NaverSeriesCrawler()

    print(f'\n🔐 네이버 로그인...')
    crawler.start_driver()

    try:
        if not crawler.login():
            print('❌ 로그인 실패')
            return

        print(f'✅ 로그인 성공')
        print(f'\n📡 단건 상세 크롤링...')

        detail = crawler.crawl_detail_with_retry(pc_url)
        if not detail:
            print('❌ 크롤링 실패')
            return

        print(f'   ↳ {detail.get("works_name")} | {detail.get("works_type")} | {detail.get("genre")}')

        with JSONLWriter(platform='naver_series', mode='custom_url') as writer:
            writer.write(detail)

        print(f'✅ 완료: {writer.count}건 저장')

    finally:
        crawler.close_driver()


def _crawl_kakao(url: str, page_type: str, count: int | None) -> None:
    """카카오페이지 커스텀 크롤링.

    page_type == 'content' → /content/<id> 단건 상세 1건 크롤
    page_type == 'list'    → 목록 페이지에서 작품 URL 수집 후 각 상세 크롤
    """
    crawler = KakaoCrawler()

    print(f'\n🔐 카카오 로그인...')
    crawler.start_driver()

    try:
        if not crawler.login():
            print('❌ 로그인 실패')
            return

        print(f'✅ 로그인 성공')

        if page_type == 'content':
            work_urls = [url]
            print(f'\n📡 단건 상세 크롤링...')
        else:
            print(f'\n📡 목록에서 작품 URL 수집...')
            work_urls = [
                w['source_url']
                for w in _fetch_kakao_works(crawler, url, count)
                if w.get('source_url')
            ]

        if not work_urls:
            print('❌ 크롤링할 URL이 없습니다.')
            return

        print(f'\n📝 JSONL 저장 중... ({len(work_urls)}건)')

        with JSONLWriter(platform='kakao_page', mode='custom_url') as writer:
            for work_url in work_urls:
                detail = crawler.crawl_detail_with_retry(work_url)
                if detail:
                    writer.write(detail)

        print(f'✅ 완료: {writer.count}건 저장')

    finally:
        crawler.close_driver()


def _crawl_ridibooks(url: str, page_type: str, count: int | None) -> None:
    """리디북스 커스텀 크롤링.

    page_type == 'book'     → /books/<id> 단건 상세 1건 크롤
    page_type == 'category' → 베스트셀러 등 카테고리 목록에서 URL 수집 후 각 상세 크롤
    """
    crawler = RidibooksCrawler()

    print(f'\n🔐 리디북스 로그인...')
    crawler.start_driver()

    try:
        if not crawler.login():
            print('❌ 로그인 실패')
            return
        print(f'✅ 로그인 성공')

        if page_type == 'book':
            book_urls = [url]
            print(f'\n📡 단건 상세 크롤링...')
        else:
            # 카테고리 URL → base + query 로 분리해 목록 수집
            sp = urlsplit(url)
            base_url = urlunsplit((sp.scheme, sp.netloc, sp.path, '', ''))
            print(f'\n📡 카테고리 목록 수집...')
            book_urls = crawler.get_category_urls(base_url, sp.query, count or 500)

        if not book_urls:
            print('❌ 크롤링할 URL이 없습니다.')
            return

        print(f'\n📝 JSONL 저장 중... ({len(book_urls)}건)')
        with JSONLWriter(platform='ridibooks', mode='custom_url') as writer:
            for book_url in book_urls:
                detail = crawler.crawl_detail_with_retry(book_url)
                if detail:
                    writer.write(detail)

        print(f'✅ 완료: {writer.count}건 저장')

    finally:
        crawler.close_driver()


def _fetch_naver_works(crawler, url: str, tab: str, count: int | None) -> list:
    """네이버에서 작품 URL 목록 수집."""
    crawler.driver.get(url)
    
    works = []
    max_items = count or 500  # 기본 500개
    
    # 무한 스크롤 시뮬레이션
    import time
    from selenium.webdriver.common.by import By
    
    last_height = crawler.driver.execute_script("return document.body.scrollHeight")
    
    while len(works) < max_items:
        # 모든 작품 링크 수집
        elements = crawler.driver.find_elements(By.CSS_SELECTOR, "a.item")
        
        for elem in elements:
            try:
                href = elem.get_attribute('href')
                if href and 'titleId=' in href:
                    work = {'source_url': href}
                    if work not in works:
                        works.append(work)
                        if len(works) >= max_items:
                            break
            except:
                continue
        
        if len(works) >= max_items:
            break
        
        # 스크롤
        crawler.driver.execute_script("window.scrollBy(0, window.innerHeight);")
        time.sleep(1)
        
        new_height = crawler.driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height
    
    return works[:max_items]


def _fetch_kakao_works(crawler, url: str, count: int | None) -> list:
    """카카오페이지에서 작품 URL 목록 수집."""
    crawler.driver.get(url)
    
    works = []
    max_items = count or 500  # 기본 500개
    
    import time
    from selenium.webdriver.common.by import By
    
    last_height = crawler.driver.execute_script("return document.body.scrollHeight")
    
    while len(works) < max_items:
        # 모든 작품 링크 수집
        elements = crawler.driver.find_elements(By.CSS_SELECTOR, "a[href*='/content/']")
        
        for elem in elements:
            try:
                href = elem.get_attribute('href')
                if href and '/content/' in href:
                    work = {'source_url': href}
                    if work not in works:
                        works.append(work)
                        if len(works) >= max_items:
                            break
            except:
                continue
        
        if len(works) >= max_items:
            break
        
        # 스크롤
        crawler.driver.execute_script("window.scrollBy(0, window.innerHeight);")
        time.sleep(1)
        
        new_height = crawler.driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height
    
    return works[:max_items]
