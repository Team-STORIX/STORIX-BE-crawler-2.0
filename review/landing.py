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
    (re.compile(r'novel\.naver\.com/webnovel/[^?#]*\?(?:.*&)?novelId=(\d+)'),
     'https://novel.naver.com/webnovel/list?novelId={}', 'NAVER_NOVEL'),
    (re.compile(r'series\.naver\.com/(novel|comic)/detail\.series\?(?:.*&)?productNo=(\d+)'),
     'https://series.naver.com/{}/detail.series?productNo={}', 'NAVER_SERIES'),
    (re.compile(r'page\.kakao\.com/content/(\d+)'),
     'https://page.kakao.com/content/{}', 'KAKAO_PAGE'),
    (re.compile(r'ridibooks\.com/books/(\d+)'),
     'https://ridibooks.com/books/{}', 'RIDIBOOKS'),
)


def canonical_landing_url(url: str) -> str:
    url = (url or '').strip()
    for pattern, template, _ in _RULES:
        m = pattern.search(url)
        if m:
            return template.format(*m.groups())
    return url


def platform_work_key(url: str) -> tuple[str, str] | None:
    """작품 링크 → (플랫폼, 플랫폼 작품 ID). 수집 이력(works_source)의 키. 모르는 주소면 None."""
    for pattern, _, platform in _RULES:
        m = pattern.search(url or '')
        if m:
            return platform, m.groups()[-1]
    return None
