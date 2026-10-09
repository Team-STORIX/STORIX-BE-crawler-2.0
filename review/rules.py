"""Layer 1 결정론적 룰 + Layer 1.5 런 단위 서킷 브레이커.

LLM 을 쓰지 않는다. 기계적으로 판정되는 건 여기서 끝낸다.

판정
  REJECTED      작품을 특정할 수 없다 (제목·작가·플랫폼·URL). 고쳐서 살릴 수 없는 건
  NEEDS_REVIEW  작품은 특정되는데 Works 에 넣을 값이 비거나 enum 에 안 맞는다 → Layer 2 / 사람
  AUTO_PASS     전부 통과. normalized 를 그대로 import 할 수 있다
"""
from collections import Counter
from dataclasses import dataclass, field

from review.catalog import EnumCatalog

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
ENUM_FIELDS = {
    'platform': ('platform', REJECTED),
    'genre': ('genre', NEEDS_REVIEW),
    'age_classification': ('ageClassification', NEEDS_REVIEW),
    'works_type': ('worksType', NEEDS_REVIEW),
}

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
    normalized['hashtags'] = [t.strip() for t in item.get('hashtags') or [] if isinstance(t, str) and t.strip()]
    violations: list[dict] = []

    for f in IDENTITY_FIELDS:
        if not normalized[f]:
            violations.append(_violation(f, 'MISSING', None, REJECTED))

    url = normalized['source_url']
    if url and not url.startswith(('http://', 'https://')):
        violations.append(_violation('source_url', 'INVALID_URL', url, REJECTED))

    for f, (kind, severity) in ENUM_FIELDS.items():
        raw = _clean(item.get(f))
        name = catalog.resolve(kind, raw)
        normalized[f] = name
        if not raw:
            violations.append(_violation(f, 'MISSING', None, severity))
        elif name is None:
            violations.append(_violation(f, 'UNKNOWN_ENUM', raw, severity))

    for f in NOT_NULL_FIELDS:
        if not normalized[f]:
            violations.append(_violation(f, 'MISSING', None, NEEDS_REVIEW))

    for f, limit in MAX_LENGTH.items():
        if len(normalized[f]) > limit:
            violations.append(_violation(f, 'TOO_LONG', len(normalized[f]), NEEDS_REVIEW))

    if any(v['severity'] == REJECTED for v in violations):
        status = REJECTED
    elif violations:
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
