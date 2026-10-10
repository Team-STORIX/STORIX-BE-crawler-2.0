"""Layer 1 결정론적 룰 + Layer 1.5 런 단위 서킷 브레이커.

LLM 을 쓰지 않는다. 기계적으로 판정되는 건 여기서 끝낸다.

판정
  REJECTED      작품을 특정할 수 없다 (제목·작가·플랫폼·URL). 고쳐서 살릴 수 없는 건
  NEEDS_REVIEW  작품은 특정되는데 Works 에 넣을 값이 비거나 enum 에 안 맞는다 → Layer 2 / 사람
  AUTO_PASS     전부 통과. normalized 를 그대로 import 할 수 있다
"""
import re
from collections import Counter
from dataclasses import dataclass, field

from review.artists import normalize_artists
from review.catalog import EnumCatalog
from review.hashtags import clean_hashtags
from review.landing import canonical_landing_url

AUTO_PASS = 'AUTO_PASS'
NEEDS_REVIEW = 'NEEDS_REVIEW'
REJECTED = 'REJECTED'

# 비면 작품을 특정할 수 없다 → REJECTED
IDENTITY_FIELDS = ('works_name', 'artist_name', 'source_url')

# Works NOT NULL 이라 비면 import 못 한다 → NEEDS_REVIEW
NOT_NULL_FIELDS = ('description', 'thumbnail_url')

# Works 컬럼 길이 (BE Works.java)
MAX_LENGTH = {
    'works_name': 255,
    'artist_name': 255,
    'author': 100,
    'illustrator': 100,
    'original_author': 100,
    'thumbnail_url': 500,
}

# enum 필드: (카탈로그 kind, 못 맞추면 판정)
# platform 은 크롤러 코드가 상수로 넣는 값이라 안 맞으면 크롤러 버그다
# 비어도 import 를 막지 않는 값. 기존 작품에 붙으면 BE 가 빈 값을 무시하고(연령은 올리기만),
# 새로 만들어야 하면 BE 가 FAILED("… 값이 비어 있습니다")를 돌려줘 그때 검수 대기로 돌린다 (importer)
# 리디 웹툰은 연령 공지가 거의 없고 네이버 웹소설은 19세만 표시하며, 시리즈 독자층 분류는 장르가 아니다
OPTIONAL_ON_UPDATE = {'genre', 'age_classification'}
INFO = 'INFO'  # 기록만 하고 판정에는 영향 없음

ENUM_FIELDS = {
    'platform': ('platform', REJECTED),
    'genre': ('genre', NEEDS_REVIEW),
    'age_classification': ('ageClassification', NEEDS_REVIEW),
    'works_type': ('worksType', NEEDS_REVIEW),
}

# 플랫폼 장르 표기 → BE 카탈로그 장르. 카탈로그 이름 · 값과 다르게 쓰는 플랫폼 표기만 둔다
GENRE_ALIASES = {
    '무협/사극': '무협',  # 네이버 웹툰 (2026-10 장르 이름 변경)
}
# 장르가 아니라 독자층 분류(네이버 시리즈 웹툰). 장르로 쓰지 않고 비운다 — 빈 장르는 BE 가 덮어쓰지 않아
# 같은 작품의 네이버 웹툰 · 카카오 · 리디 장르가 유지된다. '순정' 을 로맨스로 매핑하면 로판 작품이 로맨스가 된다
NOT_A_GENRE = {'순정', '소년', '소녀', '청년'}

# 정식 계약 전 작품 (네이버 웹툰 도전만화·베스트도전, 네이버 웹소설 베스트리그·챌린지리그). 받지 않는다
PRE_CONTRACT_URL = re.compile(r'comic\.naver\.com/(?:challenge|bestChallenge)/|novel\.naver\.com/(?:best|challenge)/')

# 작품명 끝의 권수 표기. 권 단위 묶음 상품 이름이라 작품명에서 뗀다 (2026-10-10 사용자 결정)
#   '테이밍(The Taming) 2권' · '… 3~5권' · '… (1~3권)'
# '권' 이 없는 숫자 범위는 떼지 않는다. 네이버 웹툰은 '사이드킥 2~3' · '하이브 1~2' 처럼 시즌을 범위로 쓰고,
# 떼면 시즌 1('사이드킥')과 같은 작품으로 붙는다. 시즌 · 부도 BE 가 다른 작품으로 보므로 그대로 둔다
_VOLUME_SUFFIX = re.compile(r'\s*(?:\d+\s*[~\-]\s*\d+\s*권|\d+\s*권|[(\[]\s*\d+(?:\s*[~\-]\s*\d+)?\s*권\s*[)\]])\s*$')


# 판매 형태 라벨. 작품명이 아니므로 뗀다 ('마도조사 [19세 완전판][단행본]' → '마도조사 [19세 완전판]').
# 판본 표기([19세 완전판] · [개정판])는 BE 가 다른 작품으로 보므로 남긴다
_FORMAT_LABEL = re.compile(r'\s*\[(?:단행본|e북|연재)\]')


def strip_volume_suffix(name: str) -> str:
    stripped = _VOLUME_SUFFIX.sub('', _FORMAT_LABEL.sub('', name or '')).strip()
    return stripped or (name or '')


# 작품명 자리에 들어오면 잘못 읽은 것: 상세 페이지의 섹션 제목 · 사이트 이름
# (2026-10 카카오 화면 개편 때 작품명이 전부 "줄거리"로 수집됨)
NOT_A_TITLE = {'줄거리', '작품소개', '작품 소개', '소개', '키워드', '상세정보', '작품정보', '작품 정보', '동일작',
               '카카오페이지', '콘텐츠홈', '네이버 웹툰', '네이버 웹소설', '네이버 시리즈', '리디', '리디북스'}

# 표지가 아닌 썸네일. 비율이 다른 작품과 안 맞거나 작품을 알아볼 수 없어 사람이 확인한다
#  - 네이버 웹소설 og:image 정사각형(type=n200_200): 작가가 따로 올리는 공유용 이미지
#  - 네이버 웹소설 장르 기본 표지(romance_320_12.png 등): 작가가 표지를 안 올린 작품
SQUARE_THUMBNAIL = re.compile(r'novel-phinf\.pstatic\.net/.+[?&]type=n\d+_\d+')
# 19금 가림 이미지. 성인 인증이 안 된 화면에서 표지 대신 나온다. 표지로 쓰면 안 되므로 비우고 사람이 본다
#  - 리디 static.ridicdn.net/…/book_cover/cover_adult.png (크롤러는 책 ID 로 실제 표지를 다시 받는다)
#  - 카카오페이지 로그인 · 성인 인증이 풀린 화면의 og:image (사이트 공용 공유 이미지)
AGE_GATE_COVER = re.compile(r'book_cover/cover_adult|cover_adult\.|adult_cover|thumb_adult|/adult/(?:cover|thumb)'
                            r'|page\.kakaocdn\.net/pageweb/shared/ogImage')
DEFAULT_COVER = re.compile(r'novel-phinf\.pstatic\.net/20221231_\d+/novel_\w+_PNG/[a-z]+_(?:320_)?\d+\.png')

TEXT_FIELDS = (
    'works_name', 'artist_name', 'author', 'illustrator', 'original_author',
    'description', 'thumbnail_url', 'source_url',
)


@dataclass
class Verdict:
    status: str
    normalized: dict
    violations: list[dict] = field(default_factory=list)


def _clean(value) -> str:
    return value.strip() if isinstance(value, str) else ''


def _violation(field_name: str, code: str, value, severity: str) -> dict:
    return {'field': field_name, 'code': code, 'value': value, 'severity': severity}


def validate_item(item: dict, catalog: EnumCatalog) -> Verdict:
    normalized: dict = {f: _clean(item.get(f)) for f in TEXT_FIELDS}
    # 작가명은 크롤러마다 형식이 달라 한 형식('a, b')으로 맞춘다 (역할 라벨 제거, 이름 단위 중복 제거)
    normalized.update(normalize_artists(item))
    normalized['works_name'] = strip_volume_suffix(normalized['works_name'])
    # 작품 링크는 플랫폼별 대표 주소로 맞춘다 (BE 에 landingUrl 로 저장되고, staging 중복 판정 키로도 쓴다)
    normalized['source_url'] = canonical_landing_url(normalized['source_url'])
    violations: list[dict] = []

    for f in IDENTITY_FIELDS:
        if not normalized[f]:
            violations.append(_violation(f, 'MISSING', None, REJECTED))

    url = normalized['source_url']
    if url and not url.startswith(('http://', 'https://')):
        violations.append(_violation('source_url', 'INVALID_URL', url, REJECTED))
    if PRE_CONTRACT_URL.search(url):
        violations.append(_violation('source_url', 'PRE_CONTRACT_WORK', url, REJECTED))
    if normalized['works_name'] in NOT_A_TITLE:
        violations.append(_violation('works_name', 'NOT_A_TITLE', normalized['works_name'], REJECTED))

    for f, (kind, severity) in ENUM_FIELDS.items():
        raw = _clean(item.get(f))
        if f == 'genre':
            raw = '' if raw in NOT_A_GENRE else GENRE_ALIASES.get(raw, raw)
        name = catalog.resolve(kind, raw)
        normalized[f] = name
        if not raw:
            violations.append(_violation(f, 'MISSING', None, INFO if f in OPTIONAL_ON_UPDATE else severity))
        elif name is None:
            violations.append(_violation(f, 'UNKNOWN_ENUM', raw, severity))

    # 장르와 같은 태그 · 플랫폼 태그 · 표기만 다른 중복을 뺀다 (장르가 정해진 뒤에 한다)
    normalized['hashtags'] = clean_hashtags(item.get('hashtags'), normalized['genre'], catalog)

    for f in NOT_NULL_FIELDS:
        if not normalized[f]:
            violations.append(_violation(f, 'MISSING', None, NEEDS_REVIEW))

    thumb = normalized['thumbnail_url']
    if AGE_GATE_COVER.search(thumb):
        # 빈 값으로 보내면 BE 가 기존 표지를 덮어쓰지 않는다. 가림 표지가 나왔다면 성인 작품일 가능성이 크다
        normalized['thumbnail_url'] = ''
        violations.append(_violation('thumbnail_url', 'AGE_GATE_COVER', thumb, NEEDS_REVIEW))
    elif DEFAULT_COVER.search(thumb):
        violations.append(_violation('thumbnail_url', 'DEFAULT_COVER', thumb, NEEDS_REVIEW))
    elif SQUARE_THUMBNAIL.search(thumb):
        violations.append(_violation('thumbnail_url', 'SQUARE_THUMBNAIL', thumb, NEEDS_REVIEW))

    for f, limit in MAX_LENGTH.items():
        if len(normalized[f]) > limit:
            violations.append(_violation(f, 'TOO_LONG', len(normalized[f]), NEEDS_REVIEW))

    if any(v['severity'] == REJECTED for v in violations):
        status = REJECTED
    elif any(v['severity'] == NEEDS_REVIEW for v in violations):
        status = NEEDS_REVIEW
    else:
        status = AUTO_PASS
    return Verdict(status, normalized, violations)


# ---------------------------------------------------------------------------
# Layer 1.5 — 런 단위 서킷 브레이커
#
# 건별로는 못 잡는 게 있다. 연령이 20/20 전부 "18세 이용가" 면 값 하나하나는 유효하지만
# 파서가 깨진 것이다. 분포를 봐야 잡힌다.
#
# 장르 힌트로 긁는 목록(리디 BL 스테디 등)은 원래 한 장르라 오탐이 난다.
# 그래서 걸리면 버리지 않고 런을 보류만 한다. 사람이 보고 /runs/{id}/release 로 푼다.
# ---------------------------------------------------------------------------

MIN_RUN_SIZE = 20             # 이보다 적으면 분포 규칙은 건너뛴다 (표본이 작아 오탐만 난다)
MAX_SINGLE_AGE_RATIO = 0.8
MAX_EMPTY_ARTIST_RATIO = 0.1
MAX_COUNT_DELTA = 0.5


def check_run(items: list[dict], prev_count: int | None = None) -> list[str]:
    """보류 사유 목록. 비어 있으면 통과."""
    reasons: list[str] = []
    n = len(items)

    if prev_count and abs(n - prev_count) / prev_count > MAX_COUNT_DELTA:
        reasons.append(f'수집 건수 {n}건이 직전 런 {prev_count}건 대비 ±{int(MAX_COUNT_DELTA * 100)}% 밖')

    if n < MIN_RUN_SIZE:
        return reasons

    empty_artist = sum(1 for it in items if not _clean(it.get('artist_name')))
    if empty_artist / n > MAX_EMPTY_ARTIST_RATIO:
        reasons.append(f'artist_name 빈 값 {empty_artist}/{n}건 — 작가 파서 깨짐 의심')

    ages = Counter(_clean(it.get('age_classification')) for it in items)
    top_age, top_age_count = ages.most_common(1)[0]
    if top_age_count / n > MAX_SINGLE_AGE_RATIO:
        reasons.append(f'age_classification "{top_age or "(빈 값)"}" 이 {top_age_count}/{n}건 — 연령 파서 깨짐 의심')

    genres = Counter(_clean(it.get('genre')) for it in items)
    if len(genres) == 1:
        only = next(iter(genres))
        reasons.append(f'genre 가 전부 "{only or "(빈 값)"}" — 장르 파서 깨짐 의심 (장르 고정 목록이면 release)')

    return reasons
