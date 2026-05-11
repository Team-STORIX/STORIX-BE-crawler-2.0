import queue
from concurrent.futures import ThreadPoolExecutor, as_completed
from selenium.common.exceptions import InvalidSessionIdException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

from config import GENRES, BASE_URL, GENRE_MAP, DAILY_PLUS_URL, COMPLETED_URL, TOP_300_URL
from modules.crawler.naver_crawler import NaverCrawler
from modules.crawler.kakao_crawler import KakaoCrawler
from crawler.output.jsonl_writer import JSONLWriter

WORKERS = 2
TARGET_LIMIT = 1000


def _build_naver_workers() -> list:
    workers = []
    for i in range(WORKERS):
        w = NaverCrawler(headless=True)
        w.start_driver()
        if w.login_with_cookies():
            workers.append(w)
            print(f'  ✅ 네이버 워커 {i+1}/{WORKERS} 준비')
        else:
            print(f'  ⚠️  네이버 워커 {i+1} 쿠키 로그인 실패')
            w.close_driver()
    return workers


def _build_kakao_workers() -> list:
    workers = []
    for i in range(WORKERS):
        w = KakaoCrawler(headless=True)
        w.start_driver()
        if w.login_with_cookies():
            workers.append(w)
            print(f'  ✅ 카카오 워커 {i+1}/{WORKERS} 준비')
        else:
            print(f'  ⚠️  카카오 워커 {i+1} 쿠키 로그인 실패')
            w.close_driver()
    return workers


def _make_worker_q(workers: list) -> queue.Queue:
    q = queue.Queue()
    for w in workers:
        q.put(w)
    return q


def _close_all(lead, workers):
    for w in workers:
        try:
            w.close_driver()
        except Exception:
            pass
    if lead:
        try:
            lead.close_driver()
        except Exception:
            pass


def _run_naver_genres(writer: JSONLWriter, lead: NaverCrawler, worker_q: queue.Queue, n: int):
    def crawl_url(args):
        url, genre_code = args
        w = worker_q.get()
        try:
            data = w.crawl_detail_with_retry(url)
            if data and genre_code == '로판':
                data['genre'] = '로판'
            w.human_pause(1.0, 2.5)
            return data
        except Exception as e:
            w._log.warning('크롤링 실패 (%s): %s', url, e)
            return None
        finally:
            worker_q.put(w)

    with ThreadPoolExecutor(max_workers=n) as executor:
        futures = {}
        for genre_code in GENRES:
            ko = GENRE_MAP.get(genre_code, genre_code)
            print(f'\n=== [URL 수집: {genre_code} ({ko})] ===')
            urls = lead.get_genre_urls(BASE_URL + genre_code)
            print(f'📊 {len(urls)}개 URL 큐에 추가')
            for url in urls:
                futures[executor.submit(crawl_url, (url, genre_code))] = url

        total = len(futures)
        done = 0
        for future in as_completed(futures):
            data = future.result()
            done += 1
            print(f'[{done}/{total}] 수집 중...', end='\r')
            if data:
                writer.write(data)


def _run_naver_daily_plus(writer: JSONLWriter, lead: NaverCrawler, worker_q: queue.Queue, n: int):
    target_urls = []
    while True:
        try:
            target_urls = lead.get_genre_urls(DAILY_PLUS_URL)
            print(f'📊 [매일+] {len(target_urls)}개')
            break
        except (InvalidSessionIdException, WebDriverException):
            print('⚠️  목록 수집 중 세션 끊김. 5초 후 재시도...')
            lead.close_driver()
            time.sleep(5)
            lead.start_driver()
            lead.login()

    def crawl_one(url):
        w = worker_q.get()
        try:
            return w.crawl_detail_with_retry(url)
        except Exception:
            return None
        finally:
            w.human_pause(1.0, 2.5)
            worker_q.put(w)

    total = len(target_urls)
    with ThreadPoolExecutor(max_workers=n) as executor:
        for i, data in enumerate(executor.map(crawl_one, target_urls), 1):
            print(f'[매일+ {i}/{total}]', end='\r')
            if data:
                writer.write(data)


def _scroll_and_collect(driver, limit=TARGET_LIMIT) -> list:
    item_xpath = "//div[@id='content']/div[1]/ul/li/a"
    prev = len(driver.find_elements(By.XPATH, item_xpath))
    stuck = 0
    while True:
        if prev >= limit:
            break
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1.0)
        curr = len(driver.find_elements(By.XPATH, item_xpath))
        if curr > prev:
            prev = curr
            stuck = 0
        else:
            stuck += 1
            if stuck >= 3:
                break
            driver.execute_script("window.scrollBy(0, -1500);")
            time.sleep(0.7)
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1.5)
    elems = driver.find_elements(By.XPATH, item_xpath)
    urls = [e.get_attribute('href') for e in elems[:limit] if e.get_attribute('href') and 'titleId=' in e.get_attribute('href')]
    return list(dict.fromkeys(urls))


def _run_naver_completed(writer: JSONLWriter, lead: NaverCrawler, worker_q: queue.Queue, n: int):
    def crawl_batch(urls):
        def crawl_one(url):
            w = worker_q.get()
            try:
                return w.crawl_detail_with_retry(url)
            except Exception:
                return None
            finally:
                w.human_pause(1.0, 2.0)
                worker_q.put(w)
        total = len(urls)
        with ThreadPoolExecutor(max_workers=n) as executor:
            for i, data in enumerate(executor.map(crawl_one, urls), 1):
                print(f'[{i}/{total}]', end='\r')
                if data:
                    writer.write(data)

    lead.driver.get(COMPLETED_URL)
    time.sleep(2)

    for sort_idx, sort_label in [(1, '인기순'), (4, '별점순')]:
        try:
            print(f'\n=== [완결 {sort_label}] ===')
            lead.driver.get(COMPLETED_URL)
            time.sleep(3)
            btn = WebDriverWait(lead.driver, 15).until(
                EC.element_to_be_clickable((By.XPATH, f'//*[@id="content"]/div[1]/div/div[2]/button[{sort_idx}]'))
            )
            btn.click()
            time.sleep(2)
            urls = _scroll_and_collect(lead.driver)
            print(f'📊 {len(urls)}개')
            crawl_batch(urls)
        except Exception as e:
            print(f'❌ [{sort_label}] 오류: {e}')


def run_naver_webtoon(writer: JSONLWriter):
    lead = NaverCrawler()
    workers = []
    try:
        lead.start_driver()
        if not lead.login():
            raise RuntimeError('네이버 로그인 실패')

        workers = _build_naver_workers()
        if not workers:
            raise RuntimeError('사용 가능한 네이버 워커가 없습니다')
        worker_q = _make_worker_q(workers)
        n = len(workers)

        _run_naver_genres(writer, lead, worker_q, n)
        _run_naver_daily_plus(writer, lead, worker_q, n)
        _run_naver_completed(writer, lead, worker_q, n)

    finally:
        _close_all(lead, workers)


def run_kakao_page(writer: JSONLWriter):
    lead = KakaoCrawler()
    workers = []
    try:
        lead.start_driver()
        if not lead.login():
            raise RuntimeError('카카오 로그인 실패')

        urls = lead.get_list_urls(TOP_300_URL)
        print(f'📊 [카카오페이지] {len(urls)}개')

        workers = _build_kakao_workers()
        if not workers:
            raise RuntimeError('사용 가능한 카카오 워커가 없습니다')
        worker_q = _make_worker_q(workers)
        n = len(workers)

        def crawl_one(url):
            w = worker_q.get()
            try:
                return w.crawl_detail_with_retry(url)
            except Exception:
                return None
            finally:
                w.human_pause(1.0, 2.5)
                worker_q.put(w)

        total = len(urls)
        with ThreadPoolExecutor(max_workers=n) as executor:
            for i, data in enumerate(executor.map(crawl_one, urls), 1):
                print(f'[{i}/{total}]', end='\r')
                if data:
                    writer.write(data)

    finally:
        _close_all(lead, workers)


_PLATFORM_MAP = {
    'naver_webtoon': run_naver_webtoon,
    'kakao_page': run_kakao_page,
}

SUPPORTED_PLATFORMS = list(_PLATFORM_MAP.keys())


def run_initial(platform: str):
    targets = SUPPORTED_PLATFORMS if platform == 'all' else [platform]
    for p in targets:
        if p not in _PLATFORM_MAP:
            print(f'⚠️  지원하지 않는 플랫폼: {p}. 지원 목록: {SUPPORTED_PLATFORMS}')
            continue
        print(f'\n{"="*60}')
        print(f'🚀 [{p}] initial 크롤링 시작')
        print(f'{"="*60}')
        with JSONLWriter(platform=p, mode='initial') as writer:
            _PLATFORM_MAP[p](writer)
        print(f'\n✅ [{p}] 완료. {writer.count}건 저장됨.')
