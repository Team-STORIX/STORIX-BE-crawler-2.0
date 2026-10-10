"""크롤러 연령 판정 (리디 parse_ridi_age, 네이버 시리즈 _parse_info)."""
import pytest

pytest.importorskip('selenium')
from modules.crawler.ridibooks_crawler import parse_ridi_age  # noqa: E402


def page(book_id='100', adult='false', limit='"0"', notices=''):
    others = '{"id":"999","title":"다른 책","age_limit":"19","is_adult_only":true}'
    return (f'[{others},{{"id":"{book_id}","title":"x","age_limit":{limit},"is_adult_only":{adult},"filesize":"1"}}]'
            f' "notice":{{"benefits":[]}},"notices":[{notices}],"preExclusive":null')


def test_adult_only_is_18():
    assert parse_ridi_age(page(adult='true'), '100') == '18세 이용가'


def test_age_limit_19_is_18():
    assert parse_ridi_age(page(limit='"19"'), '100') == '18세 이용가'


@pytest.mark.parametrize('title,age', [('15세 이용가 안내', '15세 이용가'), ('12세 이용가 안내', '12세 이용가')])
def test_notice_sets_age(title, age):
    assert parse_ridi_age(page(notices=f'{{"content":"...","title":"{title}"}}'), '100') == age


def test_no_notice_is_unknown_not_all_ages():
    # 같은 작품도 판마다 공지가 없을 수 있어 전체연령가로 단정하지 않는다
    assert parse_ridi_age(page(), '100') == ''


def test_other_books_on_page_are_ignored():
    # 추천 · 연결 작품(id 999)의 성인 표시를 이 책 것으로 읽지 않는다
    assert parse_ridi_age(page(notices='{"title":"15세 이용가 안내"}'), '100') == '15세 이용가'


def test_unknown_book_id():
    assert parse_ridi_age(page(), '1') == ''
    assert parse_ridi_age('', '100') == ''


# ---------------------------------------------------------------- 네이버 시리즈 연령 (.end_info 줄)

@pytest.mark.parametrize('lines,age', [
    (['연재중', '무협', '글비가', '출판사러프미디어', '전체 이용가'], '전체연령가'),
    (['연재중', '무협', '글비가', '그림ARCHE, LICO', '출판사네이버웹툰', '15세 이용가'], '15세 이용가'),
    (['완결', 'BL', '글키마님', '청소년 이용불가'], '18세 이용가'),
    (['연재중', '로판', '글누군가'], ''),   # 연령 줄이 없으면 판정하지 않는다
])
def test_series_age(lines, age):
    from modules.crawler.naver_series_crawler import NaverSeriesCrawler
    crawler = NaverSeriesCrawler.__new__(NaverSeriesCrawler)
    assert crawler._parse_info(lines)['age_classification'] == age


def test_ridi_adult_cover_replaced_with_real_cover():
    from modules.crawler.ridibooks_crawler import ridi_real_cover
    gate = 'https://static.ridicdn.net/books-backend/p/21d66d/books/dist/images/book_cover/cover_adult.png'
    assert ridi_real_cover(gate, 'https://ridibooks.com/books/777063298') == \
        'https://img.ridicdn.net/cover/777063298/xxlarge'
    real = 'https://img.ridicdn.net/cover/4928002744/xxlarge#1'
    assert ridi_real_cover(real, 'https://ridibooks.com/books/4928000826') == real
