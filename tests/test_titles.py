"""제목 규칙 한 곳 (#122). 저장할 작품명과 비교용 제목을 구분한다."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.crawler.titles import clean_work_name, same_title, strip_title_labels, strip_volume_suffix  # noqa: E402


@pytest.mark.parametrize('raw,name', [
    ('화산귀환 [독점]', '화산귀환'),
    ('넷카마 펀치!!! [완결]', '넷카마 펀치!!!'),
    ('[e북] 마도조사', '마도조사'),
    ('개정판 | 블랙 스완', '블랙 스완'),
    ('천관사복 [19세 완전판] [단행본]', '천관사복 [19세 완전판]'),   # 판본은 작품명
    ('[과다분출 단편선] 네 꿈을 꿀게', '[과다분출 단편선] 네 꿈을 꿀게'),   # 묶음 이름도 작품명
])
def test_clean_work_name(raw, name):
    assert clean_work_name(raw) == name


def test_platforms_clean_work_name_the_same():
    """시리즈 · 카카오가 같은 규칙으로 작품명을 정리한다 (시리즈만 묶음 이름까지 떼던 것)."""
    from modules.crawler.kakao_crawler import parse_kakao_title
    from modules.crawler.naver_series_crawler import NaverSeriesCrawler
    raw = '[과다분출 단편선] 네 꿈을 꿀게 [완결]'
    assert NaverSeriesCrawler._clean_title(raw + '\n(총 3화/완결)') == clean_work_name(raw) == parse_kakao_title(raw, '', '')


def test_volume_suffix():
    assert strip_volume_suffix('마도조사 [19세 완전판][단행본] 3권') == '마도조사 [19세 완전판]'
    assert strip_volume_suffix('사이드킥 2~3') == '사이드킥 2~3'   # 시즌 범위는 그대로


def test_compare_strips_all_but_edition():
    assert strip_title_labels('[독점] 화산귀환 [완결]') == '화산귀환'
    assert strip_title_labels('천관사복 [19세 완전판] [단행본]') == '천관사복 [19세 완전판]'
    assert same_title('[과다분출 단편선] 네 꿈을 꿀게', '네 꿈을 꿀게')
    assert not same_title('응급 플러팅 [개정판]', '응급 플러팅')
