"""네이버 시리즈 신작 목록 · 화/권 유형 판정 (#161)."""
import pytest

from modules.crawler.naver_series_crawler import episode_unit, parse_series_list, series_works_type


def test_parse_series_list_keeps_order_and_kind():
    src = ('<a href="/comic/detail.series?productNo=3">c</a><a href="/novel/detail.series?productNo=9">n</a>'
           '<a href="/comic/detail.series?productNo=1">c</a><a href="/comic/detail.series?productNo=3">dup</a>')
    assert parse_series_list(src, 'comic') == ['https://series.naver.com/comic/detail.series?productNo=3',
                                               'https://series.naver.com/comic/detail.series?productNo=1']
    assert parse_series_list(src, 'novel') == ['https://series.naver.com/novel/detail.series?productNo=9']


@pytest.mark.parametrize('text, unit', [('...\n총 7화\n...', '화'), ('총 1권 · 완결', '권'), ('총 105 화', '화'), ('없음', '')])
def test_episode_unit(text, unit):
    assert episode_unit(text) == unit


@pytest.mark.parametrize('url, unit, expected', [
    ('https://series.naver.com/comic/detail.series?productNo=1', '화', '웹툰'),
    ('https://series.naver.com/comic/detail.series?productNo=1', '권', '만화'),
    ('https://series.naver.com/novel/detail.series?productNo=1', '화', '웹소설'),
    ('https://series.naver.com/novel/detail.series?productNo=1', '권', '단행본'),
    ('https://series.naver.com/comic/detail.series?productNo=1', '', '웹툰'),   # 못 읽으면 탭으로만
])
def test_series_works_type(url, unit, expected):
    assert series_works_type(url, unit) == expected
