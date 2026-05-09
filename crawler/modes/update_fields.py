import json
import queue
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

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

PLATFORM_URL_PATTERNS = {
    'naver_webtoon': 'comic.naver.com',
    'kakao_page': 'page.kakao.com',
}


def _load_source_urls(input_path: Path) -> dict[str, list[str]]:
    files = []
    if input_path.is_dir():
        files = list(input_path.glob('*.jsonl'))
    elif input_path.is_file():
        files = [input_path]

    if not files:
        raise FileNotFoundError(f'JSONL 파일을 찾을 수 없습니다: {input_path}')

    grouped: dict[str, list[str]] = {p: [] for p in PLATFORM_URL_PATTERNS}
    seen: set[str] = set()

    for f in files:
        with open(f, encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                url = rec.get('source_url', '')
                if not url or url in seen:
                    continue
                seen.add(url)

                platform = rec.get('platform', '')
                if platform not in grouped:
                    platform = next(
                        (p for p, pat in PLATFORM_URL_PATTERNS.items() if pat in url),
                        None,
                    )
                if platform:
                    grouped[platform].append(url)

    total = sum(len(v) for v in grouped.values())
    print(f'📂 입력 파일 {len(files)}개, 총 {total}개 URL 로드')
    for p, urls in grouped.items():
        if urls:
            print(f'   - {p}: {len(urls)}개')
    return grouped


def _recrawl_naver(urls: list[str], writer: JSONLWriter):
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
                print(f'[네이버 {done}/{total}]', end='\r')
                if data:
                    writer.write(data)
    finally:
        _close_all(lead, workers)


def _recrawl_kakao(urls: list[str], writer: JSONLWriter):
    lead = KakaoCrawler()
    workers = []
    try:
        lead.start_driver()
        if not lead.login():
            raise RuntimeError('카카오 로그인 실패')

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
                print(f'[카카오 {i}/{total}]', end='\r')
                if data:
                    writer.write(data)
    finally:
        _close_all(lead, workers)


_RECRAWL_MAP = {
    'naver_webtoon': _recrawl_naver,
    'kakao_page': _recrawl_kakao,
}


def run_update_fields(platform: str, input_path: str):
    path = Path(input_path)
    grouped = _load_source_urls(path)

    targets = list(PLATFORM_URL_PATTERNS.keys()) if platform == 'all' else [platform]

    for p in targets:
        urls = grouped.get(p, [])
        if not urls:
            print(f'⚠️  [{p}] 재크롤링할 URL이 없습니다.')
            continue
        if p not in _RECRAWL_MAP:
            print(f'⚠️  지원하지 않는 플랫폼: {p}')
            continue

        print(f'\n{"="*60}')
        print(f'🔄 [{p}] update_fields 시작 — {len(urls)}개 URL')
        print(f'{"="*60}')
        with JSONLWriter(platform=p, mode='update_fields') as writer:
            _RECRAWL_MAP[p](urls, writer)
        print(f'\n✅ [{p}] 완료. {writer.count}건 저장됨.')
