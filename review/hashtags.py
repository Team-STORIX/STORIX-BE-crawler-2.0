"""해시태그 정리 (#27, 2026-10-10 사용자 결정 B안).

| 태그 | 처리 | 예 |
|---|---|---|
| 작품 장르와 같은 장르 태그 | 뺀다 (장르 필드에 이미 있음) | 무협 작품의 "무협/사극", 판타지 작품의 "판타지" |
| BE 장르 목록에 따로 있는 장르의 표기 | 작품 장르와 다르면 원래 표기 그대로 남긴다 | 판타지 작품의 "현대판타지"(= 현판) |
| 장르명으로 끝나는 세부 장르 표기 | 합치지 않고 그대로 남긴다 | 판타지 작품의 "오컬트판타지" · "학원로맨스" |
| 플랫폼 이벤트 · 프로모션 · 분류 태그 | 뺀다 | "2025 지상최대공모전", "독자PICK", "소설원작" |

띄어쓰기 · 대소문자만 다른 태그는 처음 나온 것 하나만 남기고, 순서는 원본 순서를 지킨다.
"""
import re

from review.catalog import EnumCatalog

# BE 장르(dbValue)와 다르게 쓰는 플랫폼 장르 표기 → BE 장르 dbValue. 별개 장르(현판 · 로판)의 표기도 여기 둔다.
# 장르 필드(rules.validate_item)와 해시태그가 같이 쓴다 — 따로 두면 '로맨스판타지' 가 태그로는 로판, 장르로는 모르는 값이 됐다 (#121)
GENRE_TAG_ALIASES = {
    '현대판타지': '현판',
    '로맨스판타지': '로판',
    '무협/사극': '무협',  # 네이버 웹툰 장르 (2026-10 장르 이름 변경)
}


def genre_alias(text: str) -> str:
    """띄어쓰기는 무시한다 ('로맨스 판타지')."""
    return GENRE_TAG_ALIASES.get(''.join((text or '').split()), text)

# 작품 내용이 아니라 플랫폼이 붙이는 태그 (2026-10-10 네이버 웹툰 연재작 400개 · 수집 결과 80작품 태그 분포에서 추림)
NOISE_TAGS = {
    # 추천 · 프로모션
    '독자pick', '지금추천작', '요즘핫한추천작', '명작', '몰아보기', '드라마&영화원작웹툰',
    # 원작 · 연령 · 형식 분류 (원작 여부는 작품 정보가 아니라 판매 분류)
    '소설원작', '원작소설有', '웹소설', '웹툰', '성인웹툰', '해외작품', 'e북', '만화',
    # 레이블 · 스튜디오 이름
    '레드아이스스튜디오', '레드아이스', '블루스트링', '레드스트링',
    # 검수 연동 테스트 데이터
    '검수테스트',
}
# 공모전 · 이벤트 회차 ('2025 지상최대공모전' · '2024 최강자전' · '2025 연재직행열차'), 네이버 완결 큐레이션('완결액션')
NOISE_PATTERN = re.compile(r'^(?:\d{4})?(?:지상최대공모전|최강자전|연재직행열차)$|^완결\S+$|^\d+주년$')
# 판매 · 연재 상태 표기. 플랫폼마다 따로 거르면 같은 '단행본' · '완결' 이 어디서는 남았다 (#122).
# 띄어쓰기를 뺀 태그로 본다 ('기다리면 무료' · '10 % 할인')
SALE_PATTERN = re.compile(
    r'^(?:완결|연재(?:중|완결)?|독점|선독점|오리지널|단행본|ebook|전자책|무료|기다리면무료|대여|소장|스크롤|후방주의|월드드랍'
    r'|할인이벤트|\d+%할인|(?:별점|평점|리뷰)\d+.*|.*\d+[만천]?원(?:이상|이하|미만|초과)?.*|\d+권(?:이상|이하|미만)?|\d+~\d+권'
    r'|\d+화(?:이상|이하|미만)?|\d+[주일]이내신작|\d+\+)$', re.I)


def is_noise_tag(tag: str) -> bool:
    """작품 내용이 아니라 플랫폼 · 판매 표기인 태그. 크롤러와 검수(clean_hashtags)가 같이 쓴다."""
    k = ''.join((tag or '').split())
    return not k or k.casefold() in NOISE_TAGS or bool(NOISE_PATTERN.match(k) or SALE_PATTERN.match(k))


def _key(tag: str) -> str:
    return ''.join(tag.split()).casefold()


def _genre_of(tag: str, genres: dict[str, str]) -> str | None:
    """genres: 정규화 키 → dbValue. 세부 장르('오컬트판타지')는 장르로 합치지 않는다."""
    k = _key(tag)
    if k in genres:
        return genres[k]
    alias = genre_alias(tag)
    if alias != tag:
        return alias
    if k.endswith('물') and k[:-1] in genres:  # 판타지물 · 무협물 · 개그물
        return genres[k[:-1]]
    return None


def clean_hashtags(tags: list, genre_name: str | None, catalog: EnumCatalog) -> list[str]:
    """genre_name 은 dbValue 가 아니라 카탈로그 name (예: FANTASY)."""
    genres = {_key(v['dbValue']): v['dbValue'] for v in catalog.raw['genre']['values'] if v.get('dbValue')}
    work_genre = None
    if genre_name:
        work_genre = next((v['dbValue'] for v in catalog.raw['genre']['values'] if v['name'] == genre_name), None)

    out: list[str] = []
    seen: set[str] = set()
    for raw in tags or []:
        if not isinstance(raw, str):
            continue
        tag = raw.strip().lstrip('#').strip()
        if is_noise_tag(tag):
            continue
        if work_genre and _genre_of(tag, genres) == work_genre:
            continue
        if _key(tag) in seen:
            continue
        seen.add(_key(tag))
        out.append(tag)
    return out
