"""봄툰 크롤러 (#59)."""
import json

import pytest

pytest.importorskip('selenium')
from modules.crawler.base_crawler import HttpUnavailable, SessionExpiredError  # noqa: E402
from modules.crawler.bomtoon_crawler import _api_result, bomtoon_alias, parse_bomtoon_contents  # noqa: E402
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
    assert parse_bomtoon_contents({**DATA, 'type': 'CARTOON'}, 'u')['works_type'] == '웹툰'  # 봄툰 만화 분류


@pytest.mark.parametrize('tag', ['2/12/22일', '4일/14일/24일', '토요연재', '열흘연재', '오직봄툰', '봄툰공모전_당선웹툰', '봄티콘'])
def test_bomtoon_meta_tags(tag):
    from modules.crawler.bomtoon_crawler import BOMTOON_META_TAGS
    assert BOMTOON_META_TAGS.match(tag)


def test_api_result_codes():
    assert _api_result(json.dumps({'result': 'SUCCESS', 'data': {'a': 1}}), 'u') == {'a': 1}
    assert _api_result(json.dumps({'result': 'ERROR', 'error': {'code': 'NOT_EXIST'}}), 'u') is None
    with pytest.raises(SessionExpiredError):
        _api_result(json.dumps({'result': 'ERROR', 'error': {'code': 'ADULT_ONLY_CONTENTS'}}), 'u')
    with pytest.raises(HttpUnavailable):
        _api_result('<html>', 'u')


def test_bomtoon_links():
    assert bomtoon_alias('https://www.bomtoon.com/detail/cat_BF ') == 'cat_BF'
    assert canonical_landing_url('https://www.bomtoon.com/detail/SHK?ref=1') == 'https://www.bomtoon.com/detail/SHK'
    assert platform_work_key('https://www.bomtoon.com/detail/SHK') == ('BOMTOON', 'SHK')
    assert bomtoon_alias('https://www.bomtoon.com/my/library/my') is None  # 목록의 잘못된 링크
