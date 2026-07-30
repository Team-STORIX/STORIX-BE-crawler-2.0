# genre 는 서비스 API 가 NULL 을 허용하지 않아(장르 기반 추천 등에서 NPE) 필수로 둔다.
# 장르 비어있는 레코드는 적재하지 않고 검수큐로 보낸다.
REQUIRED_FIELDS = ['works_name', 'artist_name', 'platform', 'source_url', 'genre']

VALID_AGE = {'전체연령가', '12세 이용가', '15세 이용가', '18세 이용가', ''}

VALID_WORKS_TYPE = {'웹툰', '웹소설', ''}

VALID_PLATFORM = {'KAKAO_PAGE', 'NAVER_WEBTOON', 'NAVER_NOVEL', 'RIDIBOOKS', 'BOMTOON', 'NAVER_SERIES'}


def validate(record: dict) -> tuple[bool, list[str]]:
    errors: list[str] = []

    for field in REQUIRED_FIELDS:
        val = record.get(field, '')
        if not (val or '').strip():
            errors.append(f'필수 필드 누락: {field}')

    platform = (record.get('platform') or '').strip()
    if platform not in VALID_PLATFORM:
        errors.append(f'유효하지 않은 플랫폼: "{platform}" (허용값: {", ".join(VALID_PLATFORM)})')

    age = (record.get('age_classification') or '').strip()
    if age not in VALID_AGE:
        errors.append(f'유효하지 않은 연령 분류: "{age}"')

    works_type = (record.get('works_type') or '').strip()
    if works_type not in VALID_WORKS_TYPE:
        errors.append(f'유효하지 않은 작품 유형: "{works_type}"')

    source_url = (record.get('source_url') or '').strip()
    if source_url and not source_url.startswith('http'):
        errors.append(f'유효하지 않은 source_url 형식: "{source_url}"')

    return len(errors) == 0, errors
