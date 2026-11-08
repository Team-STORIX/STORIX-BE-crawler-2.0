"""작품 링크(landingUrl) 정규화.

크롤러는 상세 페이지를 열 때 쓴 주소를 그대로 source_url 로 낸다. 검색 · 목록에서 들어온 주소라
추적용 쿼리가 붙거나 모양이 제각각이다. 플랫폼마다 작품 id 만 뽑아 대표 주소 하나로 맞춘다.
id 를 못 찾으면 원래 주소를 그대로 둔다.
"""
import re

# (주소 패턴, 대표 주소, 플랫폼). 플랫폼은 BE Platform enum name
_RULES = (
    (re.compile(r'comic\.naver\.com/webtoon/[^?#]*\?(?:.*&)?titleId=(\d+)'),
     'https://comic.naver.com/webtoon/list?titleId={}', 'NAVER_WEBTOON'),
    (re.compile(r'series\.naver\.com/(novel|comic)/detail\.series\?(?:.*&)?productNo=(\d+)'),
     'https://series.naver.com/{}/detail.series?productNo={}', 'NAVER_SERIES'),
    (re.compile(r'(?:page\.kakao|kakaopage)\.com/content/(\d+)'),   # kakaopage.com 은 옛 도메인
     'https://page.kakao.com/content/{}', 'KAKAO_PAGE'),
    # 앱 공유 링크. 앱이 없으면 웹이 아니라 스토어로 보내서 대표 주소로 바꿔 둔다
    (re.compile(r'link-page\.kakao\.com/open/content\?(?:.*&)?series_id=(\d+)'),
     'https://page.kakao.com/content/{}', 'KAKAO_PAGE'),
    (re.compile(r'ridibooks\.com/books/(\d+)'),
     'https://ridibooks.com/books/{}', 'RIDIBOOKS'),
    (re.compile(r'bomtoon\.com/detail/([^/?#\s]+)'),
     'https://www.bomtoon.com/detail/{}', 'BOMTOON'),
    (re.compile(r'lezhin\.com/(?:ko/)?comic/([^/?#\s]+)'),
     'https://www.lezhin.com/ko/comic/{}', 'LEZHIN'),
    (re.compile(r'mrblue\.com/(webtoon|comic|novel)/([A-Za-z0-9_]+)'),
     'https://www.mrblue.com/{}/{}', 'MRBLUE'),
)


# 정식 계약 전 작품 (네이버 웹툰 도전만화 · 베스트도전). 받지 않는다.
# 검수(review/rules)와 네이버 크롤러가 같이 쓴다 (#122)
PRE_CONTRACT_URL = re.compile(r'comic\.naver\.com/(?:challenge|bestChallenge)/')


def is_pre_contract_url(url: str) -> bool:
    return bool(PRE_CONTRACT_URL.search(url or ''))


def canonical_landing_url(url: str) -> str:
    url = (url or '').strip()
    for pattern, template, _ in _RULES:
        m = pattern.search(url)
        if m:
            return template.format(*m.groups())
    return url


# 주소만으로 작품을 알 수 없는 앱 공유 단축 링크 (리디 앱 '공유' → link.ridi.com/<코드>). 한 번 열어 넘겨주는 주소를 본다
SHORT_LINK_HOSTS = ('link.ridi.com',)
_URL_IN_TEXT = re.compile(r'https?://[^\s<>"\']+')


def extract_urls(text: str) -> list[str]:
    """앱 공유 문구('[카카오페이지] 제목 | 작가 https://...')에서 링크만. 여러 개면 순서대로."""
    return list(dict.fromkeys(u.rstrip('.,)]>') for u in _URL_IN_TEXT.findall(text or '')))


def resolve_landing_url(url: str, max_hops: int = 3) -> str:
    """사람이 붙여 넣은 링크 → 대표 주소. 단축 링크만 리다이렉트를 따라간다. 작품을 못 알아보면 원래 주소."""
    from urllib.parse import urljoin, urlsplit
    url = (url or '').strip()
    for _ in range(max_hops):
        if platform_work_key(url) or (urlsplit(url).hostname or '') not in SHORT_LINK_HOSTS:
            break
        nxt = _redirect_target(url)
        if not nxt:
            break
        url = urljoin(url, nxt)
    return canonical_landing_url(url)


def _redirect_target(url: str) -> str | None:
    import urllib.error
    import urllib.request
    from modules.crawler import robots
    from modules.crawler.base_crawler import USER_AGENT

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    try:
        robots.check(url, USER_AGENT)
        urllib.request.build_opener(NoRedirect).open(
            urllib.request.Request(url, headers={'User-Agent': USER_AGENT}), timeout=10).close()
    except urllib.error.HTTPError as e:
        return e.headers.get('Location') if 300 <= e.code < 400 else None
    except Exception as e:  # 못 열면 원래 주소로 둔다 — 요청 처리가 이유를 남긴다
        print(f'⚠️  단축 링크를 열지 못함 ({type(e).__name__}): {url}')
    return None


def work_id(url: str, platform: str) -> str | None:
    """그 플랫폼(BE enum name) 작품 링크면 플랫폼 작품 ID ('https://www.bomtoon.com/detail/cat_BF' → 'cat_BF')."""
    found = platform_work_key(url)
    return found[1] if found and found[0] == platform else None


def platform_work_key(url: str) -> tuple[str, str] | None:
    """(플랫폼, 플랫폼 작품 ID) — 수집 이력(works_source)의 키."""
    for pattern, _, platform in _RULES:
        m = pattern.search(url or '')
        if m:
            return platform, m.groups()[-1]
    return None
