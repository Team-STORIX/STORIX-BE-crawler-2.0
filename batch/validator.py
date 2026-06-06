REQUIRED_FIELDS = ['works_name', 'artist_name', 'platform', 'source_url']

VALID_AGE = {'전체연령가', '12세 이용가', '15세 이용가', '18세 이용가', ''}

VALID_WORKS_TYPE = {'웹툰', '웹소설', ''}

VALID_PLATFORM = {'카카오페이지', '네이버 웹툰', '네이버 웹소설', '리디북스', '네이버 시리즈'}


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
