"""제목 검색 후보 판정 (유형 구분 · 부분 일치 · 외전 제외)."""
import pytest

pytest.importorskip('selenium')
from crawler.modes.search_titles import _crawl_for_type, _crawl_types, _found_slots  # noqa: E402
from modules.crawler.base_crawler import BaseCrawler, pick_candidates  # noqa: E402
from modules.crawler.kakao_crawler import parse_kakao_search_card  # noqa: E402
from modules.crawler.ridibooks_crawler import ridi_type_hint  # noqa: E402


@pytest.mark.parametrize('query,text,stage', [
    ('감옥 게임', '감옥게임', 0),
    ('넷카마 펀치!!!', '넷카마 펀치!!! [완결]', 0),
    ('마도조사', '[e북] 마도조사', 0),
    ('레지나레나', '레지나레나 - 용서받지 못한 그대에게', 1),
    # 앞에 다른 말이 붙은 다른 작품은 부분 일치로 보지 않는다 (2026-10 E2E)
    ('헌터는 조용히 살고 싶다', '은퇴한 C급 헌터는 조용히 살고 싶다', None),
    ('천관사복', '전혀 다른 작품', None),
])
def test_title_match(query, text, stage):
    m = BaseCrawler.title_match(query, text)
    assert (m[0] if m else None) == stage


# 리디 「넷카마 펀치!!!」 검색 결과 (2026-10-10 화면)
NETKAMA = [
    ('webtoon', '넷카마 펀치!!!', '웹툰'),
    ('ebook', '[e북] 넷카마 펀치!!!', '단행본'),
    ('side', '넷카마 펀치!!! 외전2', None),
    ('serial', '넷카마 펀치!!!', None),
    ('adult', '넷카마 펀치!!! 외전2 [19세 완전판]', '웹소설'),
]


@pytest.mark.parametrize('works_type,expected', [
    ('웹툰', ['webtoon', 'serial']),
    ('웹소설', ['serial', 'ebook']),  # 연재판이 먼저, e북은 연재판이 없을 때
    ('단행본', ['ebook', 'serial', 'webtoon']),
])
def test_pick_candidates_by_type_skips_side_stories(works_type, expected):
    ranked = BaseCrawler.rank_candidates('넷카마 펀치!!!', NETKAMA)
    assert [c['url'] for c in pick_candidates(ranked, works_type)] == expected


def test_side_story_kept_when_searched_for():
    ranked = BaseCrawler.rank_candidates('넷카마 펀치!!! 외전2', NETKAMA)
    assert 'side' in [c['url'] for c in ranked]


def test_kakao_search_card():
    assert parse_kakao_search_card('작품, 넷카마 펀치!!! [완결], 15세 연령 제한, 웹소설, BL, 작가 키마님\n넷카마') == \
        ('넷카마 펀치!!! [완결]', '웹소설')
    assert parse_kakao_search_card('작품, 넷카마 펀치!!!, 기다무, 15세 연령 제한, 웹툰, BL') == ('넷카마 펀치!!!', '웹툰')
    assert parse_kakao_search_card('작품, 넷카마 펀치!!! [단행본] [완결], 웹소설')[1] == '단행본'


@pytest.mark.parametrize('card,hint', [
    ('[e북] 마도조사\n묵향동후 외 1명B-Lab(비랩)해외 소설\n총 9권', '단행본'),
    ('마도조사\n광풍취고당 외 1명B-Lab(비랩코믹스)BL 웹툰\n총 259화', '웹툰'),
    ('넷카마 펀치!!!\n키마님딥블렌드현대물\n총 138화', None),  # 웹소설 카드엔 장르만 있다 → 상세에서 판정
    ('환생왕\n작가\n총 300화\n소개글에 웹툰이라는 말이 있어도', None),
])
def test_ridi_type_hint(card, hint):
    assert ridi_type_hint(card) == hint


class _FakeCrawler:
    def __init__(self, types):
        self.types, self.opened = types, []

    def crawl_detail_with_retry(self, url):
        self.opened.append(url)
        return {'works_type': self.types[url], 'source_url': url}


def test_crawl_for_type_checks_detail_type():
    # 검색 화면에 유형이 안 보이는 후보는 상세에서 확인하고, 다르면 다음 후보로 간다
    cands = [{'url': 'a', 'text': 'x', 'type_hint': None, 'kind': '정확'},
             {'url': 'b', 'text': 'x', 'type_hint': None, 'kind': '정확'}]
    crawler = _FakeCrawler({'a': '웹툰', 'b': '웹소설'})
    crawled = {}
    assert [r['source_url'] for r in _crawl_for_type(crawler, cands, '웹소설', crawled)] == ['b']
    assert [r['source_url'] for r in _crawl_for_type(crawler, cands, '웹툰', crawled)] == ['a']
    assert crawler.opened == ['a', 'b']  # 이미 연 후보는 다시 열지 않는다


def test_crawl_for_type_gives_up_after_max_tries():
    cands = [{'url': str(i), 'text': 'x', 'type_hint': None, 'kind': '정확'} for i in range(5)]
    crawler = _FakeCrawler({str(i): '웹툰' for i in range(5)})
    assert _crawl_for_type(crawler, cands, '웹소설', {}) == []
    assert len(crawler.opened) == 3


def test_edition_collected_with_main_side_story_not():
    # 판본(19세 완전판 · 개정판)은 본편과 함께 수집, 외전은 수집하지 않는다 (2026-10-10 결정)
    raw = [('main', '천관사복', '웹툰'), ('adult', '천관사복 [19세 완전판]', '웹툰'),
           ('rev', '천관사복 [개정판]', '웹툰'), ('side', '천관사복 외전', '웹툰')]
    ranked = BaseCrawler.rank_candidates('천관사복', raw)
    crawler = _FakeCrawler({'main': '웹툰', 'adult': '웹툰', 'rev': '웹툰', 'side': '웹툰'})
    got = [r['source_url'] for r in _crawl_for_type(crawler, ranked, '웹툰', {}, '천관사복')]
    assert got[0] == 'main' and set(got) == {'main', 'adult', 'rev'}


def test_edition_title_is_its_own_match():
    assert BaseCrawler.title_match('천관사복', '천관사복 [19세 완전판]')[0] == 1  # 본편과 같은 제목이 아니다


def test_ebook_age_not_sent_for_web_novel():
    # 리디에 웹소설 연재판이 없어 e북을 대신 고르면 e북 연령(18세)이 웹소설 연령을 덮어쓰지 않게 비운다
    cands = [{'url': 'ebook', 'text': '[e북] 마도조사', 'type_hint': '단행본', 'kind': '정확'}]

    class Crawler:
        def crawl_detail_with_retry(self, url):
            return {'works_type': '웹소설', 'source_url': url, 'age_classification': '18세 이용가'}

    crawled = {}
    [r] = _crawl_for_type(Crawler(), cands, '웹소설', crawled)
    assert r['age_classification'] == ''
    assert crawled['ebook']['age_classification'] == '18세 이용가'  # 단행본 섹션에서 쓸 원본은 그대로
    [r] = _crawl_for_type(Crawler(), cands, '단행본', crawled)
    assert r['age_classification'] == '18세 이용가'


@pytest.mark.parametrize('query,text', [
    ('상수리나무 아래', '상수리나무 아래 4컷 만화'),       # 다른 작품 (2026-10 E2E)
    ('아, 쫌 참으세요 영주님!', '아, 쫌 참으세요 영주님! 2부'),  # 시즌은 BE 가 다른 작품으로 본다
])
def test_prefix_followed_by_other_words_is_not_a_match(query, text):
    assert BaseCrawler.title_match(query, text) is None


@pytest.mark.parametrize('query,text', [
    ('레지나레나', '레지나레나 - 용서받지 못한 그대에게'),
    ('천관사복', '천관사복 [19세 완전판]'),
    ('테이밍', '테이밍(The Taming)'),
])
def test_prefix_followed_by_subtitle_is_a_match(query, text):
    assert BaseCrawler.title_match(query, text)[0] == 1


@pytest.mark.parametrize('sections,platform,expected', [
    ({'전체'}, 'kakao_page', ['웹소설', '웹툰']),
    ({'전체'}, 'naver_webtoon', ['웹툰']),      # 네이버 웹툰은 웹툰만
    ({'전체'}, 'naver_novel', ['웹소설']),
    ({'웹툰', '전체'}, 'ridibooks', ['웹소설', '웹툰']),
    ({'웹툰'}, 'naver_series', ['웹툰']),
])
def test_all_section_expands_to_webtoon_and_novel(sections, platform, expected):
    assert _crawl_types(sections, platform) == expected


def test_all_section_is_filled_by_either_version():
    assert _found_slots('넷카마 펀치!!!', {'전체'}, {'works_type': '웹툰'}) == {('넷카마 펀치!!!', '전체')}
    assert _found_slots('넷카마 펀치!!!', {'웹툰', '전체'}, {'works_type': '웹툰'}) == \
        {('넷카마 펀치!!!', '웹툰'), ('넷카마 펀치!!!', '전체')}


def test_titles_file_sections(tmp_path, capsys):
    from cli import _parse_titles_file
    f = tmp_path / 'titles.txt'
    f.write_text('## 웹툰\n천관사복\n## 전체\n넷카마 펀치!!!\n## 단행본\n군림천하\n', encoding='utf-8')
    assert _parse_titles_file(f) == [('천관사복', '웹툰'), ('넷카마 펀치!!!', '전체'), ('군림천하', '웹소설')]
    assert '단행본' in capsys.readouterr().out  # 없어진 섹션은 경고한다


def test_ridi_revised_prefix_matches_title():
    # 리디 e북 '개정판 | 블랙 스완' 은 검색어 '블랙 스완' 과 정확히 일치로 본다 (2026-10-10 검색 결과 없음으로 빠졌음)
    assert BaseCrawler.title_match('블랙 스완', '개정판 | 블랙 스완') == (0, 1.0)
