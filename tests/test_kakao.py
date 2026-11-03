"""카카오 상세 페이지의 작품명 · 줄거리 판정."""
import pytest

pytest.importorskip('selenium')
from modules.crawler.kakao_crawler import clean_kakao_synopsis, kakao_genre, parse_kakao_title, parse_selected_tab  # noqa: E402


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


@pytest.mark.parametrize('category, expected', [
    ('웹소설BL', 'BL'), ('웹소설 판타지', '판타지'), ('웹툰 로판', '로판'), ('만화 드라마', '드라마'),
    ('웹소설', ''), ('웹툰', ''),
])
def test_kakao_genre(category, expected):
    assert kakao_genre(category) == expected


def test_parse_selected_tab_reads_menu_tab_first():
    """메뉴 탭이 하위 탭('전체')보다 먼저 나온다. 없는 탭 번호면 기본 탭이 선택돼 '지금핫한' 이 나온다 (#159)"""
    texts = ['요일연재\n총 5개 중 5번째', '실시간 랭킹\n탭, 총 19개 중 4번째',
             '선택됨,\n오늘신작\n탭, 총 19개 중 5번째', '선택됨,\n전체\n탭, 총 8개 중 1번째']
    assert parse_selected_tab(texts) == '오늘신작'
    assert parse_selected_tab(['선택됨,\n지금핫한\n탭, 총 19개 중 1번째']) == '지금핫한'
    assert parse_selected_tab(['선택됨,\n실시간 랭킹\n탭, 총 16개 중 2번째']) == '실시간 랭킹'
    assert parse_selected_tab(['오늘신작', None, '']) == ''
