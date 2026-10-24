"""필드 갱신 모드의 주소 → 플랫폼 (#105)."""
import json

import pytest

pytest.importorskip('selenium')
from crawler.modes.update_fields import SUPPORTED_PLATFORMS, _load_source_urls, platform_of_url  # noqa: E402


@pytest.mark.parametrize('url, expected', [
    ('https://comic.naver.com/webtoon/list?titleId=12345&tab=mon', 'naver_webtoon'),
    ('https://page.kakao.com/content/69430939/?tab_type=overview', 'kakao_page'),
    ('https://ridibooks.com/books/1286008286', 'ridibooks'),
    ('https://www.lezhin.com/ko/comic/pianpian', 'lezhin'),
    ('https://www.mrblue.com/webtoon/wt_bossbaby_wz', 'mrblue'),
    ('https://series.naver.com/novel/detail.series?productNo=9579028', 'naver_series'),
    ('https://example.com/x', None),
])
def test_platform_of_url(url, expected):
    assert platform_of_url(url) == expected


def test_load_groups_supported_only(tmp_path):
    rows = [{'source_url': 'https://page.kakao.com/content/1'},
            {'source_url': 'https://www.lezhin.com/ko/comic/abc'},
            {'source_url': 'https://series.naver.com/novel/detail.series?productNo=2'},   # 재크롤 함수 없음
            {'source_url': 'https://www.bomtoon.com/detail/abc'},                          # 링크로만 수집 (#149)
            {'source_url': 'https://page.kakao.com/content/1'}]                          # 중복
    (tmp_path / 'a.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows), encoding='utf-8')
    grouped = _load_source_urls(tmp_path)
    assert set(grouped) == set(SUPPORTED_PLATFORMS)
    assert grouped['kakao_page'] == ['https://page.kakao.com/content/1']
    assert grouped['lezhin'] == ['https://www.lezhin.com/ko/comic/abc']
    assert grouped['bomtoon'] == ['https://www.bomtoon.com/detail/abc']
    assert 'naver_series' not in grouped
