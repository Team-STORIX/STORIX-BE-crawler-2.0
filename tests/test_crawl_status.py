"""수집 실패 상태 코드 · 요청 간격 · 차단 · 세션 만료 · 목록 0건 (#6)."""
import pytest

pytest.importorskip('selenium')
from crawler import report  # noqa: E402
from modules.crawler.base_crawler import (  # noqa: E402
    AuthExpiredError, BaseCrawler, RateLimitedError, SessionExpiredError,
)


@pytest.fixture(autouse=True)
def clean_report():
    report.reset()
    yield
    report.reset()


class FakeDriver:
    def __init__(self, pages=None):
        self.pages = pages or {}
        self.visited = []
        self.current_url = ''
        self.title = ''

    def get(self, url):
        self.visited.append(url)
        self.current_url = url
        self.title = self.pages.get(url, '')

    def execute_script(self, js):
        return ''


class Crawler(BaseCrawler):
    REPORT_PLATFORM = 'test'
    REQUEST_INTERVAL = 0.0
    LOGIN_URL_MARKERS = ('login',)

    def __init__(self, details=None, relogin=True, pages=None):
        super().__init__()
        self.driver = FakeDriver(pages)
        self._wrap_get()
        self.details = list(details or [])
        self.relogin = relogin

    def crawl_detail(self, url):
        step = self.details.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    def close_driver(self):
        pass

    def start_driver(self):
        pass

    def login_with_cookies(self):
        return self.relogin

    def login(self):
        return False

    def get_genre_urls(self, url):
        self.driver.get(url)
        return []


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr('modules.crawler.base_crawler.time.sleep', lambda s: None)


def test_success_is_recorded():
    c = Crawler([{'works_name': 'x'}])
    assert c.crawl_detail_with_retry('u') == {'works_name': 'x'}
    assert report.summary()['by_platform'] == {'test': {'SUCCESS': 1}}
    assert not report.failed()


def test_detail_failure_is_detail_not_found_not_fatal():
    c = Crawler([None, None, None])
    assert c.crawl_detail_with_retry('u') is None
    assert report.summary()['by_platform'] == {'test': {'DETAIL_NOT_FOUND': 1}}
    assert not report.failed()  # 그 작품만 건너뛴다


def test_rate_limit_backs_off_then_recovers():
    c = Crawler([RateLimitedError('403'), {'works_name': 'x'}])
    assert c.crawl_detail_with_retry('u') == {'works_name': 'x'}
    assert c._interval >= 2.0  # 간격을 늘렸다
    assert c._rate_limits == 0  # 성공하면 연속 횟수 초기화


def test_repeated_rate_limit_stops_platform():
    c = Crawler([RateLimitedError('403')] * 3)
    with pytest.raises(RateLimitedError):
        c.crawl_detail_with_retry('u')
    assert report.failed()
    assert report.summary()['by_platform']['test'] == {'RATE_LIMITED': 1}


def test_session_expired_and_relogin_fails_stops_platform():
    c = Crawler([SessionExpiredError('login redirect')], relogin=False)
    with pytest.raises(AuthExpiredError):
        c.crawl_detail_with_retry('u')
    assert report.summary()['by_platform']['test'] == {'AUTH_EXPIRED': 1}


def test_session_expired_with_relogin_retries():
    c = Crawler([SessionExpiredError('login redirect'), {'works_name': 'x'}], relogin=True)
    assert c.crawl_detail_with_retry('u') == {'works_name': 'x'}
    assert not report.failed()


def test_block_page_raises_rate_limited():
    c = Crawler(pages={'https://r/1': '429 Too Many Requests'})
    with pytest.raises(RateLimitedError):
        c.driver.get('https://r/1')


@pytest.mark.parametrize('url,status', [
    ('https://site/genre?x=1', 'PARSING_FAILED'),   # 셀렉터 · 구조 문제
    ('https://site/login?next=genre', 'AUTH_EXPIRED'),  # 로그인 화면으로 튕김
])
def test_empty_list_is_reported(url, status):
    c = Crawler()
    assert c.get_genre_urls(url) == []
    assert report.summary()['by_platform']['test'] == {status: 1}
    assert report.failed()


def test_request_interval_is_enforced(monkeypatch):
    slept = []
    monkeypatch.setattr('modules.crawler.base_crawler.time.sleep', lambda s: slept.append(s))
    c = Crawler()
    c._interval = 2.0
    c.driver.get('https://a')
    c.driver.get('https://b')
    assert slept and max(slept) > 1.5


def test_report_saved(tmp_path):
    report.record('ridibooks', report.SUCCESS)
    report.record('ridibooks', report.SEARCH_FAILED, '없는 작품')
    path = report.save(tmp_path, 'search_titles')
    import json
    data = json.loads(path.read_text(encoding='utf-8'))
    assert data['by_platform'] == {'ridibooks': {'SEARCH_FAILED': 1, 'SUCCESS': 1}}
    assert data['failed'] is False
    assert data['failures'][0]['target'] == '없는 작품'
