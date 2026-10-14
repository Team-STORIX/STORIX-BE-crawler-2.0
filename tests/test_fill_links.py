"""링크 채우기 후보 고르기 (판본 · 단행본 · 링크 중복)."""
import pytest

pytest.importorskip('selenium')
from modules.crawler.base_crawler import BaseCrawler  # noqa: E402
from tools.fill_links import decide  # noqa: E402

K = 'https://page.kakao.com/content/'
EUNGEUP = [('64370147', '응급 플러팅', '웹소설'), ('68813187', '응급 플러팅 [단행본]', '단행본'),
           ('69067528', '응급 플러팅 [개정판]', '웹툰'), ('69049596', '응급 플러팅 [19세 완전판]', '웹툰')]


def _decide(name, wtype, age, raw, used=None, works_id=1):
    r = {'platform': 'KAKAO_PAGE', 'works_type': wtype, 'works_name': name, 'age': age, 'works_id': works_id}
    cands = BaseCrawler.rank_candidates(name, [(K + i, t, h) for i, t, h in raw])
    return decide(None, r, cands, {} if used is None else used)


@pytest.mark.parametrize('name, wtype, age, expected', [
    ('응급 플러팅', '웹툰', '18세 이용가', '69049596'),           # 표기 없는 옛 작품 · 18세 → 19세 완전판
    ('응급 플러팅', '웹툰', '15세 이용가', '69067528'),           # 15세 → 개정판
    ('응급 플러팅 [개정판]', '웹툰', '15세 이용가', '69067528'),
    ('응급 플러팅', '웹소설', '15세 이용가', '64370147'),         # 연재판이 있으면 단행본은 고르지 않는다
])
def test_edition_and_book(name, wtype, age, expected):
    status, url, _, _ = _decide(name, wtype, age, EUNGEUP)
    assert (status, url) == ('OK', K + expected)


def test_main_edition_preferred_over_editions():
    raw = [('2', 'X', '웹툰'), ('3', 'X [19세 완전판]', '웹툰')]
    assert _decide('X', '웹툰', '18세 이용가', raw)[1] == K + '2'


def test_book_used_when_no_serial():
    assert _decide('마도조사', '웹소설', '15세 이용가', [('1', '마도조사 [단행본]', '단행본')])[1] == K + '1'


def test_series_edition_with_episode_tail():
    """시리즈 원래 제목엔 '(총 N화/완결)' 이 붙는다. 정리한 제목으로 판본을 고른다"""
    from modules.crawler.naver_series_crawler import NaverSeriesCrawler
    url = 'https://series.naver.com/novel/detail.series?productNo=14384193'
    raw = '두 공작이 탐한 백작영애 [개정판]\n(총 102화/완결)'
    cands = BaseCrawler.rank_candidates('두 공작이 탐한 백작영애', [(url, NaverSeriesCrawler._clean_title(raw), '웹소설')])
    for c in cands:
        c['raw'], c['book'] = raw.replace('\n', ' '), False
    r = {'platform': 'NAVER_SERIES', 'works_type': '웹소설', 'works_name': '두 공작이 탐한 백작영애', 'age': '전체연령가', 'works_id': 6475}
    assert decide(None, r, cands, {})[:2] == ('OK', url)


def test_link_of_other_work_is_not_taken():
    used = {K + '69067528': 4217}
    status, url, _, note = _decide('응급 플러팅', '웹툰', '15세 이용가', EUNGEUP, used, works_id=9999)
    assert (status, url) == ('AMBIG', None) and '4217' in note


def test_confirmed_link_is_remembered():
    used = {}
    assert _decide('응급 플러팅 [개정판]', '웹툰', '15세 이용가', EUNGEUP, used, works_id=4217)[0] == 'OK'
    assert _decide('응급 플러팅', '웹툰', '15세 이용가', EUNGEUP, used, works_id=9999)[0] == 'AMBIG'
    assert _decide('응급 플러팅 [개정판]', '웹툰', '15세 이용가', EUNGEUP, used, works_id=4217)[0] == 'OK'
