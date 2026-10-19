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
    assert {p.login for p in platforms.PLATFORMS if p.crawler} <= set(platforms.COOKIE_FILES)


def test_landing_platforms_are_registered():
    assert {enum for _, _, enum in _RULES} == {p.enum for p in platforms.PLATFORMS}


def test_crawler_matches_key():
    pytest.importorskip('selenium')
    for p in platforms.PLATFORMS:
        if p.crawler:
            assert platforms.crawler_class(p.key).REPORT_PLATFORM == p.key


@pytest.mark.parametrize('name', ['naver_novel', 'bomtoon'])
def test_robots_blocked_platforms_are_not_crawled(name):
    # 네이버 웹소설은 사이트 전체, 봄툰은 상세 · 검색 API 를 robots.txt 가 막는다 (#131)
    assert not platforms.searchable(name)
    with pytest.raises(KeyError):
        platforms.crawler_class(name)
    assert name not in platforms.COOKIE_FILES


@pytest.mark.parametrize('name', ['naver_series', 'ridibooks'])
def test_no_search_platforms_crawl_links_only(name):
    # 시리즈는 robots.txt 가, 리디는 봇 UA 응답이 검색을 막는다. 상세(링크) 수집은 그대로 (#135)
    pytest.importorskip('selenium')
    from modules.crawler.base_crawler import BaseCrawler
    assert not platforms.searchable(name) and name not in platforms.GENERAL_SEARCH
    assert platforms.crawler_class(name).search_candidates is BaseCrawler.search_candidates
    assert '링크' in platforms.why_not_searchable(name)


def test_skip_reason_only_when_not_crawled():
    for p in platforms.PLATFORMS:
        assert bool(p.skip) == (p.crawler is None)


# ---- 공통 정규화 · 검색 (#98)

def test_artist_keys_ignore_space_paren_case():
    from review.artists import artist_keys, result_artist_keys
    assert artist_keys('관우(觀雨), Kim Min ') == {'관우', 'kimmin'}
    assert artist_keys(['관우']) & result_artist_keys({'author': '관우(觀雨)', 'artist_name': '관우(觀雨) ∙ 글'})


def test_strip_edition_labels():
    from modules.crawler.titles import strip_edition_labels
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
    monkeypatch.setattr('modules.crawler.robots.check', lambda url, ua: None)
    got = NaverCrawler().search_candidates('김부장')   # http_get 을 거친다 (요청 간격 · robots.txt)
    assert [(x['url'], x['kind']) for x in got] == [('https://comic.naver.com/webtoon/list?titleId=1', '정확')]


# ---- 레진코믹스 · 미스터블루 검색 (#107). 검색 화면 HTML 은 2026-10-16 실제 화면에서 줄인 것

LEZHIN_PAGE = (
    '<a class="flex w-full gap-x-14" href="/ko/comic/shadow_domain"><div class="desktop:py-8 py-4 flex flex-col">'
    '<p class="lzTypography lzTypography-BodyLg text-default line-clamp-1 overflow-hidden"><em class="x">그림자의 영역</em></p>'
    '<p class="lzTypography text-soft line-clamp-1">리런</p></div></a>'
    '<a class="flex w-full gap-x-14" href="/ko/comic/shadow_domain_15"><div class="desktop:py-8 py-4 flex flex-col">'
    '<p class="lzTypography lzTypography-BodyLg text-default line-clamp-1 overflow-hidden"><em class="x">그림자의 영역</em> [개정판]</p>'
    '</div></a>'
    '<a href="/ko/comic/candy_yumyum_15"><span>1</span>캔디 윰윰 [개정판]</a>'   # 인기 순위 카드는 결과가 아니다
)
MRBLUE_PAGE = '''
<li data-pid="wt_bossbaby_wz" data-section="WT"><div class="img adultmark"><a href="/webtoon/wt_bossbaby_wz"></a></div>
  <div class="txt-box"><span class="tit two-line">
      <a href="/webtoon/wt_bossbaby_wz" title="보스 빗치 베이비"><span class="label-category mr5 webtoon">웹툰</span> 보스 빗치 베이비</a>
  </span><span class="name"><a href="/author?id=3887" title="닥토">닥토</a></span></div></li>
<li><a href="/webtoon/original">오리지널</a></li>
<li data-pid="C000065505"><div class="txt-box"><span class="tit">
  <a href="/comic/C000065505" title="보스 빗치 베이비 [단행본]">만화</a></span></div></li>
<li data-pid="nv_1"><div class="txt-box"><span class="tit">
  <a href="/novel/nv_1" title="톰 &amp; 제리">소설</a></span></div></li>
'''


def test_parse_lezhin_search():
    from modules.crawler.lezhin_crawler import parse_lezhin_search
    assert parse_lezhin_search(LEZHIN_PAGE) == [
        ('https://www.lezhin.com/ko/comic/shadow_domain', '그림자의 영역', '웹툰'),
        ('https://www.lezhin.com/ko/comic/shadow_domain_15', '그림자의 영역 [개정판]', '웹툰')]


def test_parse_mrblue_search():
    from modules.crawler.mrblue_crawler import parse_mrblue_search
    assert parse_mrblue_search(MRBLUE_PAGE) == [
        ('https://www.mrblue.com/webtoon/wt_bossbaby_wz', '보스 빗치 베이비', '웹툰'),
        ('https://www.mrblue.com/comic/C000065505', '보스 빗치 베이비 [단행본]', '단행본'),
        ('https://www.mrblue.com/novel/nv_1', '톰 & 제리', '웹소설')]


def test_session_tools_match_registry():
    """exe 로 따로 빌드하는 세션 등록 툴은 등록부를 못 불러온다. 대응표가 등록부와 같아야 한다 (#122)."""
    import ast
    from pathlib import Path
    from modules.crawler import platforms
    src = (Path(__file__).parent.parent / 'tools' / 'register_session.py').read_text(encoding='utf-8')
    table = next(ast.literal_eval(n.value) for n in ast.parse(src).body
                 if isinstance(n, ast.Assign) and getattr(n.targets[0], 'id', '') == 'PLATFORMS')
    assert {k: (v[2], v[4]) for k, v in table.items()} == {
        k: (cookie, platforms.cookie_file(k).name) for k, cookie in platforms.LOGIN_COOKIES.items()}


def test_general_search_targets_are_one_list():
    from crawler.modes import requests, search_titles
    from modules.crawler import platforms
    assert requests.FALLBACK_KEYS == search_titles._ALL_TARGETS == list(platforms.GENERAL_SEARCH)


@pytest.mark.parametrize('name', ['naver_webtoon', 'naver_series', 'kakao_page', 'ridibooks', 'lezhin', 'mrblue'])
def test_server_cannot_open_login_screen(name, monkeypatch):
    """쿠키가 만료되면 서버(Docker)는 크롤러와 상관없이 같은 방식(AuthExpiredError)으로 알린다 (#77 · #131)"""
    pytest.importorskip('selenium')
    from modules.crawler.base_crawler import AuthExpiredError
    monkeypatch.setenv('DOCKER_ENV', 'true')
    crawler = platforms.crawler_class(name)()
    with pytest.raises(AuthExpiredError, match=f'--platform {platforms.get(name).login}'):
        crawler.no_manual_login_on_server()
