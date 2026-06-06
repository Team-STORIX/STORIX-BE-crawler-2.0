import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import NAVER_NEW_URL, KAKAO_NEW_URL, RIDIBOOKS_NEW_TARGETS, NAVER_NOVEL_NEW_URL
from modules.crawler.naver_crawler import NaverCrawler
from modules.crawler.kakao_crawler import KakaoCrawler
from modules.crawler.ridibooks_crawler import RidibooksCrawler
from modules.crawler.naver_novel_crawler import NaverNovelCrawler
from crawler.output.jsonl_writer import JSONLWriter
from crawler.modes.initial import (
    _build_naver_workers,
    _build_naver_novel_workers,
    _build_kakao_workers,
    _make_worker_q,
    _close_all,
)

WORKERS = 3


def _make_dedup_writer(writer: JSONLWriter):
    """source_url 기준 중복 방지 래퍼. 스레드 안전."""
    seen: set[str] = set()
    lock = threading.Lock()

    def write_if_new(data: dict) -> bool:
        url = (data.get('source_url') or '').strip()
        key = url or f"{data.get('works_name', '')}|{data.get('artist_name', '')}"
        with lock:
            if key in seen:
                return False
            seen.add(key)
        writer.write(data)
        return True

    return write_if_new


def run_naver_webtoon(writer: JSONLWriter):
    write_if_new = _make_dedup_writer(writer)
    lead = NaverCrawler()
    workers = []
    try:
        lead.start_driver()
        if not lead.login():
            raise RuntimeError('네이버 로그인 실패')

        print(f'\n=== [네이버 신작 URL 수집] ===')
        urls = lead.get_genre_urls(NAVER_NEW_URL)
        print(f'📊 {len(urls)}개 URL 수집')

        workers = _build_naver_workers()
        if not workers:
            raise RuntimeError('사용 가능한 네이버 워커가 없습니다')
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
            futures = {executor.submit(crawl_one, url): url for url in urls}
            done = 0
            for future in as_completed(futures):
                data = future.result()
                done += 1
                print(f'[{done}/{total}] 수집 중...', end='\r')
                if data:
                    write_if_new(data)

    finally:
        _close_all(lead, workers)


def run_kakao_page(writer: JSONLWriter):
    write_if_new = _make_dedup_writer(writer)
    lead = KakaoCrawler()
    workers = []
    try:
        lead.start_driver()
        if not lead.login():
            raise RuntimeError('카카오 로그인 실패')

        print(f'\n=== [카카오 신작 URL 수집] ===')
        urls = lead.get_list_urls(KAKAO_NEW_URL)
        print(f'📊 {len(urls)}개 URL 수집')

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
                    write_if_new(data)

    finally:
        _close_all(lead, workers)


def run_naver_novel(writer: JSONLWriter):
    write_if_new = _make_dedup_writer(writer)
    lead = NaverNovelCrawler()
    workers = []
    try:
        lead.start_driver()
        if not lead.login():
            raise RuntimeError('네이버 웹소설 로그인 실패')

        print(f'\n=== [네이버 웹소설 신작 URL 수집] ===')
        urls = lead.get_genre_urls(NAVER_NOVEL_NEW_URL)
        print(f'📊 {len(urls)}개 URL 수집')

        workers = _build_naver_novel_workers()
        if not workers:
            raise RuntimeError('사용 가능한 네이버 웹소설 워커가 없습니다')
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
            futures = {executor.submit(crawl_one, url): url for url in urls}
            done = 0
            for future in as_completed(futures):
                data = future.result()
                done += 1
                print(f'[{done}/{total}] 수집 중...', end='\r')
                if data:
                    write_if_new(data)

    finally:
        _close_all(lead, workers)


def run_ridibooks(writer: JSONLWriter):
    write_if_new = _make_dedup_writer(writer)
    crawler = RidibooksCrawler()
    try:
        crawler.start_driver()
        if not crawler.login():
            raise RuntimeError('리디북스 로그인 실패')

        for base_url, extra_params, label, max_count, genre_hint, works_type in RIDIBOOKS_NEW_TARGETS:
            print(f'\n=== [리디북스 신작: {label}] ===')
            urls = crawler.get_category_urls(base_url, extra_params, max_count)
            print(f'📊 {len(urls)}개')

            for i, url in enumerate(urls, 1):
                print(f'[{label} {i}/{len(urls)}]', end='\r')
                data = crawler.crawl_detail_with_retry(url)
                if data:
                    data['genre'] = genre_hint
                    data['works_type'] = works_type
                    write_if_new(data)
                crawler.human_pause(1.0, 2.5)

    finally:
        try:
            crawler.close_driver()
        except Exception:
            pass


_PLATFORM_MAP = {
    'naver_webtoon': run_naver_webtoon,
    'naver_novel': run_naver_novel,
    'kakao_page': run_kakao_page,
    'ridibooks': run_ridibooks,
}

SUPPORTED_PLATFORMS = list(_PLATFORM_MAP.keys())


def run_new_works(platform: str):
    targets = SUPPORTED_PLATFORMS if platform == 'all' else [platform]
    for p in targets:
        if p not in _PLATFORM_MAP:
            print(f'⚠️  지원하지 않는 플랫폼: {p}. 지원 목록: {SUPPORTED_PLATFORMS}')
            continue
        print(f'\n{"="*60}')
        print(f'🚀 [{p}] new_works 크롤링 시작')
        print(f'{"="*60}')
        with JSONLWriter(platform=p, mode='new_works') as writer:
            _PLATFORM_MAP[p](writer)
        print(f'\n✅ [{p}] 완료. {writer.count}건 저장됨.')
