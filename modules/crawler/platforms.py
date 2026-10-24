"""플랫폼 등록부. 플랫폼 이름 · BE enum · 표시명 · 크롤러 · 로그인 쿠키를 여기 한 곳에서 정한다.

모드 · 툴은 플랫폼별 분기를 따로 두지 않고 여기서 크롤러를 받는다 (#98).

    with open_crawler('kakao_page') as c:                         # 쿠키 → 계정 → 사람 로그인 (crawler.login)
    with open_crawler('KAKAO_PAGE', headless=True, login='cookies', required=False) as c:
                                                                  # 쿠키로만. 실패하면 경고하고 비로그인으로 계속
"""
import importlib
from contextlib import contextmanager
from dataclasses import dataclass

from config import (KAKAO_COOKIE_FILE, LEZHIN_COOKIE_FILE, MRBLUE_COOKIE_FILE,
                    NAVER_COOKIE_FILE, RIDIBOOKS_COOKIE_FILE)


@dataclass(frozen=True)
class Platform:
    key: str        # cli --platform · 리포트 · 수집 파일 이름 (crawler.REPORT_PLATFORM)
    enum: str
    label: str
    crawler: str | None  # 'modules.crawler.<모듈>:<클래스>' (selenium 을 늦게 불러오려고 문자열로 둔다). None = 수집 안 함
    login: str      # 로그인 쿠키를 같이 쓰는 플랫폼 key (네이버 웹툰 · 웹소설 · 시리즈는 네이버 쿠키 하나)
    skip: str = ''  # 수집하지 않는 이유 (crawler=None 인 플랫폼). 노션 요청 결과에 그대로 남긴다
    no_search: str = ''  # 제목으로 찾지 않는 이유. 작품 링크로만 수집한다 (#135)


PLATFORMS = (
    Platform('naver_webtoon', 'NAVER_WEBTOON', '네이버 웹툰', 'modules.crawler.naver_crawler:NaverCrawler', 'naver_webtoon'),
    # 수집하지 않는 플랫폼도 BE 에 남은 링크의 이름 · 로그인 표시용으로 둔다 (#131)
    Platform('naver_novel', 'NAVER_NOVEL', '네이버 웹소설', None, 'naver_webtoon',
             '네이버 웹소설은 robots.txt 가 사이트 전체를 막아 수집하지 않습니다'),
    Platform('naver_series', 'NAVER_SERIES', '네이버 시리즈', 'modules.crawler.naver_series_crawler:NaverSeriesCrawler', 'naver_webtoon',
             no_search='네이버 시리즈는 robots.txt 가 검색을 막아 제목으로 찾지 않습니다 — 작품 링크가 있어야 수집합니다'),
    Platform('kakao_page', 'KAKAO_PAGE', '카카오페이지', 'modules.crawler.kakao_crawler:KakaoCrawler', 'kakao_page'),
    Platform('ridibooks', 'RIDIBOOKS', '리디북스', 'modules.crawler.ridibooks_crawler:RidibooksCrawler', 'ridibooks',
             no_search='리디북스는 봇의 검색을 막아 제목으로 찾지 않습니다 — 작품 링크가 있어야 수집합니다'),
    # 상세 HTML 은 로그인 없이 받는다 (#149). login 이 비어 있으면 로그인 · 쿠키가 없는 플랫폼
    Platform('bomtoon', 'BOMTOON', '봄툰', 'modules.crawler.bomtoon_crawler:BomtoonCrawler', '',
             no_search='봄툰은 robots.txt 가 검색 API 를 막아 제목으로 찾지 않습니다 — 작품 링크가 있어야 수집합니다'),
    Platform('lezhin', 'LEZHIN', '레진코믹스', 'modules.crawler.lezhin_crawler:LezhinCrawler', 'lezhin'),
    Platform('mrblue', 'MRBLUE', '미스터블루', 'modules.crawler.mrblue_crawler:MrblueCrawler', 'mrblue'),
)

COOKIE_FILES = {
    'naver_webtoon': NAVER_COOKIE_FILE,
    'kakao_page': KAKAO_COOKIE_FILE,
    'ridibooks': RIDIBOOKS_COOKIE_FILE,
    'lezhin': LEZHIN_COOKIE_FILE,
    'mrblue': MRBLUE_COOKIE_FILE,
}

# 로그인됐음을 보여 주는 쿠키 (세션 API · 세션 등록 툴이 받은 쿠키를 확인할 때)
LOGIN_COOKIES = {
    'naver_webtoon': 'NID_AUT',
    'kakao_page': '_kawlt',
    'ridibooks': 'ridi-at',
}

# 제목만 받고 연재처를 모를 때('전체' · 노션 '기타') 찾아보는 플랫폼 — 웹툰 · 웹소설을 모두 다루는 곳
GENERAL_SEARCH = ('naver_webtoon', 'kakao_page')

_BY_NAME = {n: p for p in PLATFORMS for n in (p.key, p.enum)}


def get(name: str) -> Platform:
    """플랫폼 key('kakao_page') 나 BE enum('KAKAO_PAGE') 으로 찾는다."""
    try:
        return _BY_NAME[name]
    except KeyError:
        raise KeyError(f'모르는 플랫폼: {name}') from None


def crawler_class(name: str):
    p = get(name)
    if not p.crawler:
        raise KeyError(p.skip)
    module, cls = p.crawler.split(':')
    return getattr(importlib.import_module(module), cls)


def searchable(name: str) -> bool:
    """제목 검색 후보(search_candidates)를 내는 크롤러인지. 링크 채우기 · 복구가 같이 쓴다 (#121)"""
    from modules.crawler.base_crawler import BaseCrawler
    if not get(name).crawler or get(name).no_search:
        return False
    return crawler_class(name).search_candidates is not BaseCrawler.search_candidates


def why_not_searchable(name: str) -> str:
    """제목으로 찾지 않는 이유 (수집 안 함 · 검색 안 함). 노션 요청 결과에 남긴다"""
    p = get(name)
    return p.skip or p.no_search or f'{p.label}는 제목 검색을 지원하지 않습니다'


def cookie_file(name: str):
    return COOKIE_FILES[get(name).login]


def log_in(crawler, name: str, login: str = 'interactive') -> bool:
    """login: 'interactive' = crawler.login() (쿠키 → 계정 → 사람), 'cookies' = 쿠키로만 (사람 없이 도는 작업)."""
    p = get(name)
    if not p.login:
        return True
    if login == 'cookies':
        if not cookie_file(name).exists():
            print(f'⚠️  [{p.label}] 로그인 쿠키 없음 — python cli.py login --platform {p.login}')
            return False
        ok = crawler.login_with_cookies()
        print(f'🍪 [{p.label}] 쿠키 로그인 {"성공" if ok else "실패 (만료) — python cli.py login --platform " + p.login}')
        return ok
    return bool(crawler.login())


@contextmanager
def open_crawler(name: str, *, headless: bool = False, login: str | None = 'interactive', required: bool = True):
    """브라우저를 띄우고 로그인까지 마친 크롤러. 끝나면 브라우저를 닫는다.

    login=None 이면 로그인하지 않는다. required=True 인데 로그인에 실패하면 AuthExpiredError.
    """
    from modules.crawler.base_crawler import AuthExpiredError
    p = get(name)
    crawler = crawler_class(name)(headless=headless)
    crawler.start_driver()
    try:
        if login and not log_in(crawler, name, login) and required:
            raise AuthExpiredError(f'{p.label} 로그인 실패')
        yield crawler
    finally:
        crawler.close_driver()
