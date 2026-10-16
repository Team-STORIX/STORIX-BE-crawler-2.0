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
