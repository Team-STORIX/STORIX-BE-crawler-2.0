import re
from urllib.parse import urlparse, parse_qs
from modules.crawler.naver_crawler import NaverCrawler
from modules.crawler.kakao_crawler import KakaoCrawler
from modules.crawler.ridibooks_crawler import RidibooksCrawler
from crawler.output.jsonl_writer import JSONLWriter

SUPPORTED_PLATFORMS = ['naver_webtoon', 'kakao_page', 'ridibooks']

# 리디북스 단건 상세 URL 판별: /books/<숫자>
_RIDI_BOOK_RE = re.compile(r'/books/(\d+)')


def parse_url_to_platform(url: str) -> tuple[str, str]:
    """
    URL을 파싱해서 (platform, page_type) 반환.

    Returns:
        (platform, page_type): 예) ('naver_webtoon', 'dailyPlus'),
                               리디 단건은 ('ridibooks', 'book'),
                               리디 카테고리는 ('ridibooks', 'category')

    Raises:
        ValueError: 지원하지 않는 URL
    """
    parsed = urlparse(url)
    domain = parsed.netloc.lower()

    # 네이버 웹툰
    if 'naver.com' in domain:
        # tab 파라미터 추출
        params = parse_qs(parsed.query)
        tab = params.get('tab', [''])[0] or 'dailyPlus'
        return 'naver_webtoon', tab

    # 카카오페이지
    elif 'kakaopage.com' in domain:
        # /content/ 이후의 부분을 page_type으로 사용
        path_parts = parsed.path.split('/')
        if 'content' in path_parts:
            content_idx = path_parts.index('content')
            if content_idx + 1 < len(path_parts):
                page_type = path_parts[content_idx + 1]
                return 'kakao_page', page_type
        return 'kakao_page', 'default'

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
    elif platform == 'kakao_page':
        _crawl_kakao(url, page_type, count)
    elif platform == 'ridibooks':
        _crawl_ridibooks(url, page_type, count)


def _crawl_naver(url: str, tab: str, count: int | None) -> None:
    """네이버 웹툰 커스텀 크롤링."""
    crawler = NaverCrawler()
    
    print(f'\n🔐 네이버 로그인...')
    crawler.start_driver()
    
    try:
        if not crawler.login():
            print('❌ 로그인 실패')
            return
        
        print(f'✅ 로그인 성공')
        print(f'\n📡 {tab} 탭에서 크롤링...')
        
        works_list = _fetch_naver_works(crawler, url, tab, count)
        
        if not works_list:
            print('❌ 크롤링된 작품이 없습니다.')
            return
        
        print(f'\n📝 JSONL 저장 중... ({len(works_list)}건)')
        
        with JSONLWriter(platform='naver_webtoon', mode='custom_url') as writer:
            for work in works_list:
                detail = crawler.crawl_detail_with_retry(work.get('source_url', ''))
                if detail:
                    writer.write(detail)
        
        print(f'✅ 완료: {writer.count}건 저장')
    
    finally:
        crawler.close_driver()


def _crawl_kakao(url: str, page_type: str, count: int | None) -> None:
    """카카오페이지 커스텀 크롤링."""
    crawler = KakaoCrawler()
    
    print(f'\n🔐 카카오 로그인...')
    crawler.start_driver()
    
    try:
        if not crawler.login():
            print('❌ 로그인 실패')
            return
        
        print(f'✅ 로그인 성공')
        print(f'\n📡 {page_type}에서 크롤링...')
        
        works_list = _fetch_kakao_works(crawler, url, count)
        
        if not works_list:
            print('❌ 크롤링된 작품이 없습니다.')
            return
        
        print(f'\n📝 JSONL 저장 중... ({len(works_list)}건)')
        
        with JSONLWriter(platform='kakao_page', mode='custom_url') as writer:
            for work in works_list:
                detail = crawler.crawl_detail_with_retry(work.get('source_url', ''))
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
            from urllib.parse import urlsplit, urlunsplit
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
