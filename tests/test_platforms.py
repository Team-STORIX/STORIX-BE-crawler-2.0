"""플랫폼 등록부 (#98)."""
import pytest

from modules.crawler import platforms
from review.landing import _RULES


def test_get_by_key_or_enum():
    assert platforms.get('kakao_page') is platforms.get('KAKAO_PAGE')
    with pytest.raises(KeyError):
        platforms.get('kakao')


def test_naver_shares_one_login():
    assert {platforms.get(n).login for n in ('NAVER_WEBTOON', 'NAVER_NOVEL', 'NAVER_SERIES')} == {'naver_webtoon'}
    assert platforms.cookie_file('naver_series') == platforms.cookie_file('naver_webtoon')


def test_every_platform_has_cookie_file():
    assert {p.login for p in platforms.PLATFORMS} <= set(platforms.COOKIE_FILES)


def test_landing_platforms_are_registered():
    assert {enum for _, _, enum in _RULES} == {p.enum for p in platforms.PLATFORMS}


def test_crawler_matches_key():
    pytest.importorskip('selenium')
    for p in platforms.PLATFORMS:
        assert platforms.crawler_class(p.key).REPORT_PLATFORM == p.key


# ---- 공통 정규화 · 검색 (#98)

def test_artist_keys_ignore_space_paren_case():
    from review.artists import artist_keys, result_artist_keys
    assert artist_keys('관우(觀雨), Kim Min ') == {'관우', 'kimmin'}
    assert artist_keys(['관우']) & result_artist_keys({'author': '관우(觀雨)', 'artist_name': '관우(觀雨) ∙ 글'})


def test_strip_edition_labels():
    from modules.crawler.base_crawler import strip_edition_labels
    assert strip_edition_labels('응급 플러팅 [19세 완전판]') == '응급 플러팅'
    assert strip_edition_labels('Deep(딥) [개정판]') == 'Deep(딥)'


def test_kakao_retries_without_symbols():
    pytest.importorskip('selenium')
    from modules.crawler.kakao_crawler import KakaoCrawler
    K = 'https://page.kakao.com/content/'
    cards = {'형아 나 귀엽지': [(K + '1', '형아, 나 귀엽지?', '웹툰')]}
    c = KakaoCrawler.__new__(KakaoCrawler)
    c._search_cards = lambda q: cards.get(q, [])
    assert [x['url'] for x in c.search_candidates('형아, 나 귀엽지?')] == [K + '1']


def test_naver_webtoon_search_api(monkeypatch):
    pytest.importorskip('selenium')
    import io
    import json
    from modules.crawler.naver_crawler import NaverCrawler
    body = {'searchWebtoonResult': {'searchViewList': [{'titleId': 1, 'titleName': '김부장'},
                                                         {'titleId': 2, 'titleName': '전혀 다른 작품'}]}}
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **k: io.BytesIO(json.dumps(body).encode()))
    got = NaverCrawler.__new__(NaverCrawler).search_candidates('김부장')
    assert [(x['url'], x['kind']) for x in got] == [('https://comic.naver.com/webtoon/list?titleId=1', '정확')]
