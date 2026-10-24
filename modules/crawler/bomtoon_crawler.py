"""봄툰 크롤러 (#59). 작품 링크로만 수집한다 (#149).

작품 API · 검색 API(/api/)는 robots.txt 가 막는다 (#131). 상세 화면 /detail/{alias} 는 허용이고, 서버가 그린 HTML 의
__NEXT_DATA__ 에 작품 정보가 들어 있다. 로그인 없이 받는다:
    일반 작품  props.pageProps.ssrDetail      작품 API 응답과 같은 모양
    성인 작품  props.pageProps.openGraphData  ssrDetail 이 비어 이것만 온다. 작가 · 태그가 쉼표로 이은 문자열
표지(thumbnails)는 어느 쪽에도 없다 — 화면이 뜬 뒤 /api/ 로 받는다. 봄툰에만 있는 새 작품은 표지 값 부족으로 검수 대기가 된다.
"""
import json
import re

from .base_crawler import PASS_THROUGH, BaseCrawler, HttpUnavailable
from review.hashtags import is_noise_tag
from review.landing import work_id

SITE = 'https://www.bomtoon.com'

# 첫 태그는 장르다. 연재 요일 · 봄툰 자체 분류 · 이벤트 태그는 작품 내용이 아니라 해시태그로 쓰지 않는다
#   토요연재 · 열흘연재 · 4일/14일/24일 · 2/12/22일 · 단편선 · 오직봄툰 · 기다봄 · 웹툰판 · 월드드랍 · 봄툰공모전_당선웹툰 · BL비엘
# 봄툰만의 표기 (연재 요일 · 봄툰 브랜드 · 판 구분). 판매 · 연재 상태 공통 표기는 review.hashtags.is_noise_tag
BOMTOON_META_TAGS = re.compile(
    r'^(?:.+연재|\d+(?:일)?(?:/\d+(?:일)?)*일|단편선|오직봄툰|기다봄|웹툰판|소설판|BL비엘|GL지엘|.*공모전.*|.*봄툰.*)$')
# 작가가 직접 다는 자유 태그(isExtra)는 '츤데레수' · '혐관' 같은 키워드도 있지만 문장('드실래요_잘생긴..감자♥') ·
# 캐릭터 커플링('도해x단조')이 섞여 있다. 짧은 단어형만 남긴다
_EXTRA_KEYWORD = re.compile(r'^[가-힣A-Za-z0-9/]{1,8}$')
_PAIRING = re.compile(r'[가-힣0-9][xX×][가-힣0-9]')
# CARTOON 을 출판 만화로 봤지만(#64) 레진 연재 웹툰 녹색전상도 CARTOON 이다. 봄툰 그림 작품은 모두 웹툰 (사용자 결정 2026-10-25)
_TYPES = {'COMIC': '웹툰', 'CARTOON': '웹툰', 'NOVEL': '웹소설'}
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def _keep_tag(tag: dict) -> bool:
    name = (tag.get('name') or '').strip()
    if is_noise_tag(name) or BOMTOON_META_TAGS.match(name):
        return False
    if tag.get('isExtra'):
        return bool(_EXTRA_KEYWORD.match(name)) and not _PAIRING.search(name)
    return True


def bomtoon_alias(url: str) -> str | None:
    return work_id(url, 'BOMTOON')   # 작품 주소 규칙은 review/landing 한 곳


def parse_bomtoon_contents(data: dict, url: str) -> dict:
    tags = [t for t in data.get('tags') or [] if (t.get('name') or '').strip()]
    genre = tags[0]['name'].strip() if tags else ''
    hashtags = list(dict.fromkeys(t['name'].strip() for t in tags[1:] if _keep_tag(t)))
    # 작가 역할이 전부 AUTHOR 라 글 · 그림을 구분할 수 없다. 이름만 순서대로 낸다 (검수 단계 정규화가 나눈다)
    names = []
    for c in data.get('creators') or []:
        name = (c.get('name') or '').strip()
        if name and name not in names:
            names.append(name)
    thumbs = {t.get('type'): t.get('imagePath') for t in data.get('thumbnails') or []}
    return {
        'platform': 'BOMTOON',
        'works_name': (data.get('title') or '').strip(),
        'artist_name': ', '.join(names),
        # 성인 여부만 내려온다. 성인이 아니면 15세인지 전체인지 알 수 없어 비운다 (BE 는 빈 값을 덮어쓰지 않는다)
        'age_classification': '18세 이용가' if data.get('isAdult') else '',
        'description': (data.get('synopsis') or '').replace('\r\n', '\n').strip(),
        'genre': genre,
        'hashtags': hashtags,
        # 표지는 세로형 VERTICAL(315x415, 다른 플랫폼 표지와 같은 세로 비율). DETAIL 은 상세 화면 가로 배너(720x330),
        # ORIGINAL_TYPE_* 는 작품 속 장면 홍보 컷이라 쓰지 않는다. 세로형이 없으면 정사각 MAIN (2026-10-10 확인)
        'thumbnail_url': thumbs.get('VERTICAL') or thumbs.get('MAIN') or thumbs.get('SQUARE') or '',
        'works_type': _TYPES.get(data.get('type'), ''),
        'source_url': f"{SITE}/detail/{data.get('alias') or bomtoon_alias(url)}",
    }


def _from_open_graph(og: dict, url: str) -> dict:
    """성인 작품의 openGraphData → 작품 API 모양. 태그가 자유 태그인지 알 수 없어 장르 뒤는 자유 태그 규칙으로 거른다"""
    names = [n.strip() for n in (og.get('tags') or '').split(',') if n.strip()]
    return {
        'alias': bomtoon_alias(url), 'title': og.get('title'), 'isAdult': og.get('isAdult'),
        'type': og.get('contentsType'), 'synopsis': og.get('synopsis'),
        'creators': [{'name': n} for n in (og.get('creators') or '').split(',')],
        'tags': [{'name': n, 'isExtra': i > 0} for i, n in enumerate(names)],
    }


def parse_bomtoon_page(html: str, url: str) -> dict | None:
    """상세 HTML → 수집 결과. 없는 작품이면 None (ssrDetail · openGraphData 가 둘 다 null)"""
    m = _NEXT_DATA.search(html or '')
    if not m:
        raise HttpUnavailable('__NEXT_DATA__ 없음')
    try:
        props = json.loads(m.group(1))['props']['pageProps']
    except (ValueError, KeyError, TypeError):
        raise HttpUnavailable('__NEXT_DATA__ 형식이 다름')
    if props.get('ssrDetail'):
        return parse_bomtoon_contents(props['ssrDetail'], url)
    if props.get('openGraphData'):
        return parse_bomtoon_contents(_from_open_graph(props['openGraphData'], url), url)
    return None


class BomtoonCrawler(BaseCrawler):
    REPORT_PLATFORM = 'bomtoon'
    REQUEST_INTERVAL = 1.0

    def crawl_detail_http(self, url: str) -> dict | None:
        alias = bomtoon_alias(url)
        if not alias:
            raise HttpUnavailable('작품 주소 아님')
        html = self.http_get(f'{SITE}/detail/{alias}')
        return parse_bomtoon_page(html, url) if html is not None else None

    def crawl_detail(self, url: str) -> dict | None:
        alias = bomtoon_alias(url)
        if not alias:
            return None
        try:
            self.driver.get(f'{SITE}/detail/{alias}')
            return parse_bomtoon_page(self.driver.page_source, url)
        except PASS_THROUGH:
            raise
        except Exception as e:
            self._log.warning('crawl_detail 실패 (%s): %s', url, e)
            return None

    def login(self) -> bool:
        return True

    def login_with_cookies(self) -> bool:
        return True
