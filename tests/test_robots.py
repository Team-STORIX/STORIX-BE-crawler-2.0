"""robots.txt 검사 (#131). 네트워크 없이 규칙 해석 · 예외 · 크롤러 진입점을 본다."""
import pytest

from modules.crawler import robots
from modules.crawler.robots import RobotsDisallowed

SERIES = """User-agent: *

Allow: /

Disallow: /my/library
Disallow: /search
Disallow: /novel/categoryProductList.series
"""

COMIC = """User-agent: *
Allow: /
Disallow: /webtoon/detail
Disallow: /webtoon/list?*&page=
Disallow: /*.json$
"""

RIDI = """User-agent: *

Disallow: /api/
Disallow: /account/

User-agent: Bytespider
Disallow: /
"""


@pytest.fixture
def cached(monkeypatch):
    cache = {}
    monkeypatch.setattr(robots, '_cache', cache)
    return cache


def test_parse_reads_only_star_group():
    assert robots.parse(RIDI) == [('disallow', '/api/'), ('disallow', '/account/')]


def test_parse_prefers_our_bot_group():
    """STORIXBot 그룹이 있으면 * 대신 그 그룹만 따른다 (#135)"""
    text = RIDI + "\nUser-agent: storixbot\nDisallow: /search\n"
    assert robots.parse(text) == [('disallow', '/search')]
    assert robots.parse("User-agent: STORIXBot\nDisallow:\n\nUser-agent: *\nDisallow: /\n") == []


def test_denial_body():
    """리디가 봇 UA 검색에 주는 403 본문 (2026-10-19)"""
    assert robots.is_denial('User-agent: Mozilla/5.0 (compatible; STORIXBot/1.0; +https://github.com/Team-STORIX)\n'
                            'Disallow: /search')
    assert not robots.is_denial('<html><body>403 Forbidden</body></html>')
    assert not robots.is_denial('')


def test_consecutive_user_agents_share_rules():
    text = "User-agent: Googlebot\nUser-agent: *\nDisallow: /private\n\nUser-agent: Other\nDisallow: /\n"
    assert robots.parse(text) == [('disallow', '/private')]


@pytest.mark.parametrize('path,ok', [
    ('/search/search.series?t=all&q=a', False),
    ('/novel/detail.series?productNo=1', True),
    ('/novel/categoryProductList.series', False),
])
def test_longest_rule_wins(path, ok):
    assert robots.verdict(robots.parse(SERIES), path)[0] is ok


@pytest.mark.parametrize('path,ok', [
    ('/webtoon/list?titleId=1', True),
    ('/webtoon/list?titleId=1&page=2', False),
    ('/webtoon/detail?titleId=1&no=3', False),
    ('/a/b.json', False),
    ('/a/b.json?x=1', True),
])
def test_wildcard_and_anchor(path, ok):
    assert robots.verdict(robots.parse(COMIC), path)[0] is ok


def test_check_raises_on_blocked_site(cached):
    cached['blocked.example.com'] = [('disallow', '/')]
    with pytest.raises(RobotsDisallowed, match='Disallow: /'):
        robots.check('https://blocked.example.com/works/1', 'ua')


def test_exception_skips_only_listed_path(cached):
    cached['nid.naver.com'] = [('disallow', '/')]
    robots.check('https://nid.naver.com/nidlogin.login', 'ua')   # 예외 목록 (사람 로그인 화면)
    with pytest.raises(RobotsDisallowed):
        robots.check('https://nid.naver.com/user2/help', 'ua')


def test_series_search_is_not_an_exception(cached):
    cached['series.naver.com'] = robots.parse(SERIES)
    with pytest.raises(RobotsDisallowed):
        robots.check('https://series.naver.com/search/search.series?q=a', 'ua')


def test_user_agent_names_our_bot():
    pytest.importorskip('selenium')
    from modules.crawler.base_crawler import USER_AGENT
    assert robots.BOT_NAME in USER_AGENT and 'Chrome' not in USER_AGENT


def test_removed_platform_paths_are_not_exceptions():
    assert robots.exception_for('https://www.bomtoon.com/api/balcony-api-v2/contents/x') is None
    assert robots.exception_for('https://ridibooks.com/account/myridi') is None


def test_every_exception_has_reason():
    for host, prefix, why in robots.EXCEPTIONS:
        assert host and prefix.startswith('/') and why.strip()


def test_unreadable_robots_does_not_block(cached, monkeypatch):
    monkeypatch.setattr(robots, '_fetch', lambda host, ua: None)
    robots.check('https://example.com/anything', 'ua')
    assert cached['example.com'] is None


def test_http_get_checks_before_request(cached, monkeypatch):
    pytest.importorskip('selenium')
    import urllib.request
    from modules.crawler.base_crawler import BaseCrawler

    def no_network(*a, **k):
        raise AssertionError('막힌 경로인데 요청을 보냄')
    monkeypatch.setattr(urllib.request, 'urlopen', no_network)
    cached['blocked.example.com'] = [('disallow', '/')]
    with pytest.raises(RobotsDisallowed):
        BaseCrawler().http_get('https://blocked.example.com/works/1')


def test_http_get_stops_on_bot_denial(cached, monkeypatch):
    """robots.txt 엔 없지만 봇에게 403 으로 거절한 경로도 robots 차단처럼 멈춘다 (#135)"""
    pytest.importorskip('selenium')
    import io
    import urllib.error
    import urllib.request
    from modules.crawler.base_crawler import BaseCrawler

    def denied(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 403, 'Forbidden', {},
                                     io.BytesIO(b'User-agent: STORIXBot\nDisallow: /search'))
    monkeypatch.setattr(urllib.request, 'urlopen', denied)
    cached['ridibooks.com'] = []
    with pytest.raises(RobotsDisallowed, match='봇에게 막은'):
        BaseCrawler().http_get('https://ridibooks.com/search?q=a')


def test_ridi_token_alive():
    pytest.importorskip('selenium')
    import base64
    import json
    from modules.crawler.ridibooks_crawler import ridi_token_alive

    def jwt(exp):
        body = base64.urlsafe_b64encode(json.dumps({'exp': exp}).encode()).decode().rstrip('=')
        return f'h.{body}.s'
    assert ridi_token_alive([{'name': 'ridi-at', 'value': jwt(2000)}], now=1000)
    assert not ridi_token_alive([{'name': 'ridi-at', 'value': jwt(500)}], now=1000)
    assert not ridi_token_alive([{'name': 'ridi-at', 'value': 'not-a-jwt'}], now=1000)
    assert not ridi_token_alive([], now=1000)
