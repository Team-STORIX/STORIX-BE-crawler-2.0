"""입구 · 플랫폼이 달라도 같은 판단 (#121)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.crawler.base_crawler import AuthExpiredError, BaseCrawler, is_edition  # noqa: E402
from modules.crawler.classify import age_from_text, artist_fields, works_type_from_category  # noqa: E402


@pytest.mark.parametrize('text,age', [
    ('19세 이용가', '18세 이용가'), ('청소년 이용불가', '18세 이용가'), ('청불', '18세 이용가'), ('성인', '18세 이용가'),
    ('15세 이용가', '15세 이용가'), ('15세이용가', '15세 이용가'), ('12세 이용가', '12세 이용가'),
    ('전체 이용가', '전체연령가'), ('전체연령가', '전체연령가'), ('', ''), ('알 수 없음', ''),
])
def test_age_from_text(text, age):
    assert age_from_text(text) == age


@pytest.mark.parametrize('text,wtype', [
    ('웹툰 소설원작', '웹툰'),      # 카카오는 웹소설, 리디는 웹툰으로 갈렸다
    ('만화 e북', '만화'), ('판타지 웹소설', '웹소설'), ('로맨스 e북', '웹소설'), ('BL', '웹툰'),
])
def test_works_type_from_category(text, wtype):
    assert works_type_from_category(text) == wtype


@pytest.mark.parametrize('role,fields', [
    ('글/그림', ('author', 'illustrator')), ('그린이', ('illustrator',)), ('원작', ('original_author',)),
    ('저자', ('author',)), ('', ('author',)), ('옮긴이', ()), ('번역', ()), ('감수', ()),
])
def test_artist_fields(role, fields):
    assert artist_fields(role) == fields


@pytest.mark.parametrize('name', ['천관사복 [19세 완전판]', '[과다분출 단편선] 네 꿈을 꿀게', '화산귀환'])
def test_entries_agree_on_same_work(name):
    """노션 요청 · 검색(search_titles), 링크 채우기(fill_links), 복구(recover)가 같은 후보를 같은 작품으로 본다."""
    from crawler.modes.recover import matches_source
    query = '천관사복' if '천관사복' in name else '네 꿈을 꿀게' if '꿈' in name else '화산귀환'
    title_ok = BaseCrawler.title_match(query, name) is not None
    same_edition = is_edition(name) == is_edition(query)
    recover = matches_source({'title_snapshot': query}, query, {'works_name': name, 'artist_name': ''})
    assert recover == (title_ok and same_edition)   # 복구도 판본을 본편과 다른 작품으로 본다


def test_naver_login_checks_cookie_like_other_platforms(monkeypatch, tmp_path):
    """login() 이 쿠키를 넣은 뒤 NID_AUT 만 보던 걸 쿠키 로그인 판정(성인 작품)으로 (만료 쿠키 통과 방지)."""
    from modules.crawler import naver_crawler
    cookie = tmp_path / 'naver_cookies.pkl'
    cookie.write_bytes(b'x')
    monkeypatch.setattr(naver_crawler, 'NAVER_COOKIE_FILE', cookie)
    monkeypatch.setenv('DOCKER_ENV', 'true')
    c = naver_crawler.NaverCrawler.__new__(naver_crawler.NaverCrawler)

    class Driver:
        def get(self, url):
            pass
    c.driver = Driver()
    monkeypatch.setattr(c, 'login_with_cookies', lambda: False)
    monkeypatch.setattr(c, '_login_with_credentials', lambda: False)
    monkeypatch.setattr(c, '_is_logged_in', lambda: True)   # 넣은 쿠키라 NID_AUT 는 늘 있다
    with pytest.raises(AuthExpiredError):
        c.login()


def test_custom_url_detects_platform_like_landing():
    from crawler.modes.custom_url import parse_url_to_platform
    from crawler.modes.update_fields import platform_of_url
    for url in ('https://novel.naver.com/webnovel/list?novelId=1', 'https://www.lezhin.com/ko/comic/jinx',
                'https://series.naver.com/novel/detail.series?productNo=1', 'https://ridibooks.com/books/1'):
        assert parse_url_to_platform(url)[0] == platform_of_url(url)


def test_genre_alias_same_for_genre_and_tags():
    from review.hashtags import genre_alias
    assert genre_alias('로맨스판타지') == genre_alias('로맨스 판타지') == '로판'
