"""레진코믹스 · 미스터블루 크롤러 (#59)."""
import json

import pytest

pytest.importorskip('selenium')
from modules.crawler.lezhin_crawler import lezhin_alias, lezhin_content, parse_lezhin_content  # noqa: E402
from modules.crawler.mrblue_crawler import mrblue_work, parse_mrblue_page  # noqa: E402
from review.landing import canonical_landing_url, platform_work_key  # noqa: E402

LEZHIN = {
    'id': 7011751609, 'alias': 'junk_junk', 'rating': '19', 'genres': ['drama'], 'isAdult': True,
    'artists': [{'name': '모서리', 'role': 'scripter'}, {'name': '푸파', 'role': 'painter'},
                {'name': '오로지', 'role': 'original'}, {'name': '스튜디오', 'role': 'label'}],
    'display': {'title': '정크? 정크! (Junk? Junk!)', 'synopsis': '첫 줄\n둘째 줄'},
    'properties': {'tags': ['월드드랍', 'WorldDrop', '여성인기19', '스낵타임', '후방주의', '삼각관계', '고수위', '삼각관계']},
}


def _lezhin_page(content: dict) -> str:
    flight = json.dumps({'state': {'data': {'content': content}}}, ensure_ascii=False, separators=(',', ':'))
    flight = flight[flight.index('{"content"'):]
    half = len(flight) // 2
    return ''.join(f'<script>self.__next_f.push([1,{json.dumps(part, ensure_ascii=False)}])</script>'
                   for part in (flight[:half], flight[half:]))


def test_lezhin_content_from_flight_chunks():
    assert lezhin_content(_lezhin_page(LEZHIN))['alias'] == 'junk_junk'
    assert lezhin_content('<html>로그인</html>') is None


def test_parse_lezhin_content():
    r = parse_lezhin_content(LEZHIN, 'https://www.lezhin.com/ko/comic/junk_junk')
    assert r['works_name'] == '정크? 정크! (Junk? Junk!)'
    assert (r['author'], r['illustrator'], r['original_author']) == ('모서리', '푸파', '오로지')
    assert '스튜디오' not in r['artist_name']
    assert (r['age_classification'], r['genre'], r['works_type']) == ('18세 이용가', '드라마', '웹툰')
    assert r['hashtags'] == ['삼각관계', '고수위']
    assert r['thumbnail_url'] == 'https://ccdn.lezhin.com/v2/comics/7011751609/images/tall.jpg'  # 세로 표지


def test_lezhin_writer_draws_too():
    r = parse_lezhin_content({**LEZHIN, 'artists': [{'name': '밍과', 'role': 'writer'}]}, 'u')
    assert (r['author'], r['illustrator']) == ('밍과', '밍과')


MRBLUE_PAGE = """
<div class="txt-info"><div class="info"> <span><a href="/webtoon/genre/bl">BL</a></span> <span>매주 화 연재</span></div>
<p class="title">펀치 드렁크 러브</p> <div class="txt">
<p><span class="width35">그림</span> <span class="authorname"><a href='/author?id=1' title='옥동'>옥동</a></span><i class="linebreak"></i><span class="width35">글</span> <span class="authorname"><a href='/author?id=2' title='모스카레토'>모스카레토</a></span></p>
<p> <span>19세 이용가</span> <span><a href='/provider?keyword=x' title='바니앤드래곤'>바니앤드래곤</a></span> </p> </div></div>
<a class="keyword" href="/keywords/webtoon?id=1">#현대물</a> <a class="keyword" href="/keywords/webtoon?id=2">#미블뿐</a>
<a class="keyword" href="/keywords/webtoon?id=3">#리뷰100개&#x2B;</a> <a class="keyword" href="/keywords/webtoon?id=4">#2만~3만원</a>
<a class="keyword" href="/keywords/webtoon?id=5">#4주이내신작</a> <a class="keyword" href="/keywords/webtoon?id=6">#친구&gt;연인</a>
<div class="txt-box"> 첫 줄<br>둘째&nbsp;줄<br> </div>
"""


def test_parse_mrblue_page():
    r = parse_mrblue_page(MRBLUE_PAGE, 'https://www.mrblue.com/webtoon/wt_000078575?x=1')
    assert r['works_name'] == '펀치 드렁크 러브'
    assert (r['author'], r['illustrator'], r['artist_name']) == ('모스카레토', '옥동', '옥동, 모스카레토')
    assert (r['age_classification'], r['genre'], r['works_type']) == ('18세 이용가', 'BL', '웹툰')
    assert r['hashtags'] == ['현대물', '친구>연인']  # 판매 · 통계 태그 제외, HTML 문자 풀기
    assert r['description'] == '첫 줄\n둘째 줄'
    assert r['thumbnail_url'] == 'https://img.mrblue.com/prod_img/comics/wt_000078575/cover_w480.jpg'


def test_mrblue_writer_painter_and_all_ages():
    page = MRBLUE_PAGE.replace(
        """<p><span class="width35">그림</span> <span class="authorname"><a href='/author?id=1' title='옥동'>옥동</a></span><i class="linebreak"></i><span class="width35">글</span> <span class="authorname"><a href='/author?id=2' title='모스카레토'>모스카레토</a></span></p>""",
        """<p><span>그림/글</span> <span class="authorname long"><a href='/author?id=3' title='은은'>은은</a></span></p>""",
    ).replace('19세 이용가', '전체 이용가')
    r = parse_mrblue_page(page, 'https://www.mrblue.com/webtoon/wt_000071025')
    assert (r['author'], r['illustrator'], r['age_classification']) == ('은은', '은은', '전체연령가')


def test_mrblue_login_page_is_none():
    assert parse_mrblue_page('<html>로그인</html>', 'https://www.mrblue.com/webtoon/wt_1') is None


def test_links():
    assert lezhin_alias('https://www.lezhin.com/ko/comic/kiss_me_if_you_can') == 'kiss_me_if_you_can'
    assert platform_work_key('https://www.lezhin.com/ko/comic/jinx') == ('LEZHIN', 'jinx')
    assert mrblue_work('https://www.mrblue.com/webtoon/wt_bossbaby_wz') == ('webtoon', 'wt_bossbaby_wz')
    assert canonical_landing_url('https://www.mrblue.com/webtoon/wt_000070142?a=1') == 'https://www.mrblue.com/webtoon/wt_000070142'
    assert platform_work_key('https://www.mrblue.com/webtoon/wt_000070142') == ('MRBLUE', 'wt_000070142')
