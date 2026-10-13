"""카카오 상세 페이지의 작품명 · 줄거리 판정."""
import pytest

pytest.importorskip('selenium')
from modules.crawler.kakao_crawler import clean_kakao_synopsis, parse_kakao_title  # noqa: E402


@pytest.mark.parametrize('og,doc,big,expected', [
    ('군림천하', '군림천하 - 웹소설 | 카카오페이지', '군림천하', '군림천하'),
    # 화면이 그려지기 전: og:title · 문서 제목이 사이트 기본값
    ('카카오페이지', '콘텐츠홈 | 카카오페이지', '넷카마 펀치!!!', '넷카마 펀치!!!'),
    ('', '천관사복 - 웹툰 | 카카오페이지', '', '천관사복'),
    ('천관사복 [완결]', '', '', '천관사복'),
    # 섹션 제목은 작품명이 아니다 (2026-10 전부 "줄거리"로 수집됨)
    ('카카오페이지', '콘텐츠홈 | 카카오페이지', '줄거리', ''),
    ('', '', '', ''),
])
def test_parse_kakao_title(og, doc, big, expected):
    assert parse_kakao_title(og, doc, big) == expected


@pytest.mark.parametrize('raw,expected', [
    ('줄거리\n선락국 태자 출신의 사련은\n더보기', '선락국 태자 출신의 사련은'),
    ('줄거리\n\n용대운 문학의 결정판!', '용대운 문학의 결정판!'),
    ('선락국 태자 출신의 사련은', '선락국 태자 출신의 사련은'),
    # 본문 중간의 '줄거리' 는 건드리지 않는다
    ('이 작품의 줄거리는 단순하다', '이 작품의 줄거리는 단순하다'),
    ('', ''),
])
def test_clean_kakao_synopsis(raw, expected):
    assert clean_kakao_synopsis(raw) == expected


def test_kakao_title_keeps_edition_label():
    # [19세 완전판] 을 떼면 BE 가 본편과 같은 작품으로 보고 본편을 덮어쓴다
    assert parse_kakao_title('넷카마 펀치!!! 외전2 [19세 완전판]', '', '') == '넷카마 펀치!!! 외전2 [19세 완전판]'
