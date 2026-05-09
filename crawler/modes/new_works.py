import queue
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import NAVER_NEW_URL, KAKAO_NEW_URL
from modules.crawler.naver_crawler import NaverCrawler
from modules.crawler.kakao_crawler import KakaoCrawler
from crawler.output.jsonl_writer import JSONLWriter
from crawler.modes.initial import (
    _build_naver_workers,
    _build_kakao_workers,
    _make_worker_q,
    _close_all,
)

WORKERS = 3


def run_naver_webtoon(writer: JSONLWriter):
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
                    writer.write(data)

    finally:
        _close_all(lead, workers)


def run_kakao_page(writer: JSONLWriter):
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
                    writer.write(data)

    finally:
        _close_all(lead, workers)


_PLATFORM_MAP = {
    'naver_webtoon': run_naver_webtoon,
    'kakao_page': run_kakao_page,
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
