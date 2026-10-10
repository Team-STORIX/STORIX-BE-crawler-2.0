"""BE 대상 환경(dev / prod) 선택.

STORIX_ENV 로 고르고(기본 dev), 주소 · 키 · staging DB 는 환경별 값을 쓴다.
staging DB 를 나누는 이유: import 상태(IMPORTED, imported_works_id)가 환경마다 다르다.
같은 DB 를 쓰면 dev 로 보낸 행이 prod 로는 안 나가고, dev worksId 가 prod 기록에 섞인다.

    STORIX_ENV                      dev | prod
    STORIX_DEV_INTERNAL_API_KEY     dev 내부 API 키 (Parameter Store /storix/dev/env/INTERNAL_API_KEY)
    STORIX_PROD_INTERNAL_API_KEY    prod 내부 API 키 (Parameter Store /storix/prod/env/INTERNAL_API_KEY)
    STORIX_DEV_API_BASE_URL         선택. 기본 https://dev.storix.kr
    STORIX_PROD_API_BASE_URL        선택. 기본 https://api.storix.kr
    STAGING_DATABASE_NAME           선택. 기본 storix_staging_{env}
"""
import os

DEFAULT_BASE_URLS = {
    'dev': 'https://dev.storix.kr',
    'prod': 'https://api.storix.kr',
}


def target() -> str:
    env = os.getenv('STORIX_ENV', 'dev').strip().lower()
    if env not in DEFAULT_BASE_URLS:
        raise ValueError(f'STORIX_ENV 는 dev 또는 prod 여야 합니다: {env!r}')
    return env


def api_base_url(env: str) -> str:
    return os.getenv(f'STORIX_{env.upper()}_API_BASE_URL') or DEFAULT_BASE_URLS[env]


def internal_api_key(env: str) -> str:
    return os.getenv(f'STORIX_{env.upper()}_INTERNAL_API_KEY', '')


def staging_database(env: str) -> str:
    return os.getenv('STAGING_DATABASE_NAME') or f'storix_staging_{env}'
