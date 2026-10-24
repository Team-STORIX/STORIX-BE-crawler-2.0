"""봄툰 크롤러 (#59 · #149)."""
import json

import pytest

pytest.importorskip('selenium')
from modules.crawler.base_crawler import HttpUnavailable  # noqa: E402
from modules.crawler.bomtoon_crawler import bomtoon_alias, parse_bomtoon_contents, parse_bomtoon_page  # noqa: E402
from review.landing import canonical_landing_url, platform_work_key  # noqa: E402

DATA = {
    'alias': 'URMYWORLD', 'title': '너는 나의 세상', 'isAdult': True, 'type': 'COMIC',
    'synopsis': '[오직봄툰 공모전 당선작]\r\n어릴 적 결혼을 약속했던 소꿉친구',
    'creators': [{'name': '뿡', 'type': 'AUTHOR'}, {'name': '뿡', 'type': 'AUTHOR'}],
    'tags': [{'name': n, 'isExtra': False} for n in ('BL', '4일/14일/24일', '월드드랍', '봄툰공모전_당선웹툰',
                                                     '미인공', '오메가버스', '오직봄툰', '토요연재')]
            + [{'name': n, 'isExtra': True} for n in ('츤데레수', '도해x단조', '드실래요_잘생긴..감자♥', '미인공')],
    'thumbnails': [{'type': 'DETAIL', 'imagePath': 'https://img/detail.webp'},
                   {'type': 'MAIN', 'imagePath': 'https://img/main.webp'},
                   {'type': 'VERTICAL', 'imagePath': 'https://img/vertical.webp'}],
}


def test_parse_bomtoon_contents():
    r = parse_bomtoon_contents(DATA, 'https://www.bomtoon.com/detail/URMYWORLD?x=1')
    assert r['works_name'] == '너는 나의 세상'
    assert r['artist_name'] == '뿡'
    assert r['age_classification'] == '18세 이용가'
    assert (r['genre'], r['works_type']) == ('BL', '웹툰')
    assert r['hashtags'] == ['미인공', '오메가버스', '츤데레수']  # 연재 요일 · 봄툰 분류 · 문장 · 커플링 태그 제외
    assert r['thumbnail_url'] == 'https://img/vertical.webp'  # 세로 표지. DETAIL 은 가로 배너
    no_vertical = {**DATA, 'thumbnails': [t for t in DATA['thumbnails'] if t['type'] != 'VERTICAL']}
    assert parse_bomtoon_contents(no_vertical, 'u')['thumbnail_url'] == 'https://img/main.webp'
    assert r['description'].startswith('[오직봄툰 공모전 당선작]\n')
    assert r['source_url'] == 'https://www.bomtoon.com/detail/URMYWORLD'


def test_non_adult_age_is_left_empty_and_novel_type():
    r = parse_bomtoon_contents({**DATA, 'isAdult': False, 'type': 'NOVEL'}, 'u')
    assert (r['age_classification'], r['works_type']) == ('', '웹소설')
    assert parse_bomtoon_contents({**DATA, 'type': 'CARTOON'}, 'u')['works_type'] == '웹툰'  # 출판 만화 분류가 아니다 (#149)


@pytest.mark.parametrize('tag', ['2/12/22일', '4일/14일/24일', '토요연재', '열흘연재', '오직봄툰', '봄툰공모전_당선웹툰'])
def test_bomtoon_meta_tags(tag):
    from modules.crawler.bomtoon_crawler import BOMTOON_META_TAGS
    assert BOMTOON_META_TAGS.match(tag)


def _page(page_props: dict) -> str:
    data = json.dumps({'props': {'pageProps': page_props}, 'page': '/detail/[alias]'}, ensure_ascii=False)
    return f'<html><head><title>x</title></head><body><script id="__NEXT_DATA__" type="application/json">{data}</script></body></html>'


def test_page_uses_ssr_detail():
    """일반 작품은 ssrDetail 이 작품 API 응답과 같은 모양이다"""
    r = parse_bomtoon_page(_page({'ssrDetail': DATA, 'openGraphData': {'title': '다른 값'}}), 'https://www.bomtoon.com/detail/URMYWORLD')
    assert r == parse_bomtoon_contents(DATA, 'https://www.bomtoon.com/detail/URMYWORLD')


def test_adult_page_uses_open_graph():
    """성인 작품은 ssrDetail 이 비고 openGraphData 만 온다. 작가 · 태그가 쉼표 문자열 (녹색전상, 2026-10-25 확인)"""
    og = {'title': '녹색전상', 'thumbnail': None, 'synopsis': '“…당신을 새기고 싶습니다.”\r\n영원히',
          'creators': '잭스', 'tags': 'BL,현대물,조직/암흑가,하드코어,도해x단조,오직봄툰,드실래요_잘생긴..감자♥',
          'isAdult': True, 'contentsType': 'COMIC'}
    r = parse_bomtoon_page(_page({'ssrDetail': None, 'openGraphData': og}), 'https://www.bomtoon.com/detail/GREENLIGHT?x=1')
    assert (r['works_name'], r['artist_name'], r['age_classification']) == ('녹색전상', '잭스', '18세 이용가')
    assert (r['genre'], r['works_type']) == ('BL', '웹툰')
    assert r['hashtags'] == ['현대물', '조직/암흑가', '하드코어']   # 봄툰 분류 · 커플링 · 문장 태그 제외
    assert r['description'] == '“…당신을 새기고 싶습니다.”\n영원히'
    assert r['thumbnail_url'] == ''   # 표지는 HTML 에 없다
    assert r['source_url'] == 'https://www.bomtoon.com/detail/GREENLIGHT'
    assert parse_bomtoon_page(_page({'openGraphData': {**og, 'creators': '가, 나'}}), 'https://www.bomtoon.com/detail/G')['artist_name'] == '가, 나'


def test_missing_work_and_broken_page():
    assert parse_bomtoon_page(_page({'ssrDetail': None, 'openGraphData': None}), 'u') is None   # 없는 작품
    with pytest.raises(HttpUnavailable):
        parse_bomtoon_page('<html>점검 중</html>', 'u')
    with pytest.raises(HttpUnavailable):
        parse_bomtoon_page('<script id="__NEXT_DATA__">{"props": 1}</script>', 'u')


def test_bomtoon_links():
    assert bomtoon_alias('https://www.bomtoon.com/detail/cat_BF ') == 'cat_BF'
    assert canonical_landing_url('https://www.bomtoon.com/detail/SHK?ref=1') == 'https://www.bomtoon.com/detail/SHK'
    assert platform_work_key('https://www.bomtoon.com/detail/SHK') == ('BOMTOON', 'SHK')
    assert bomtoon_alias('https://www.bomtoon.com/my/library/my') is None  # 목록의 잘못된 링크
