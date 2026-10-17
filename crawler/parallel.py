"""플랫폼 단위 병렬 실행 (#7).

플랫폼마다 결과 파일이 따로라(JSONLWriter 기본 파일명 `<플랫폼>_<모드>.jsonl`) 동시에 돌려도 서로 덮어쓰지 않는다.
동시에 뜨는 크롬 수는 parallel × 플랫폼 안 워커 수(initial 2 · new_works 3)다.
"""
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from crawler import report
from modules.crawler.base_crawler import AuthExpiredError, RateLimitedError


def record_platform_stop(platform: str, e: Exception) -> None:
    """차단 · 세션 만료가 아닌 예외도 PARSING_FAILED 로 남겨 런 실패로 본다."""
    if isinstance(e, RateLimitedError):
        status = report.RATE_LIMITED
    elif isinstance(e, AuthExpiredError):
        status = report.AUTH_EXPIRED
    else:
        status = report.PARSING_FAILED
    report.record(platform, status, '플랫폼 중단', f'{type(e).__name__}: {e}')


def run_platforms(targets: list[str], fn: Callable[[str], None], parallel: int = 1) -> None:
    def guarded(platform: str) -> None:
        try:
            fn(platform)
        except Exception as e:
            print(f'⚠️  [{platform}] 건너뜀: {e}')
            record_platform_stop(platform, e)

    if parallel <= 1 or len(targets) <= 1:
        for p in targets:
            guarded(p)
        return
    print(f'🧵 플랫폼 {len(targets)}개를 최대 {parallel}개씩 동시에 실행')
    with ThreadPoolExecutor(max_workers=parallel, thread_name_prefix='platform') as executor:
        list(executor.map(guarded, targets))
