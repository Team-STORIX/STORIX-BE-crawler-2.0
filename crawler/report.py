"""수집 실행 리포트 — 플랫폼 · 구간별 상태 코드 집계 (크롤링 계획 문서 §9, #6).

| 코드 | 언제 | 처리 |
|---|---|---|
| SUCCESS | 정상 | |
| AUTH_EXPIRED | 로그인 페이지로 튕김 · 재로그인 실패 | 그 플랫폼 중단 |
| RATE_LIMITED | 403 · 429 차단 화면이 계속됨 | 간격을 늘려 재시도, 계속되면 그 플랫폼 중단 |
| PARSING_FAILED | 장르 · 목록 수집 0건 | 런 실패 |
| DETAIL_NOT_FOUND | 상세 수집 실패 · 내려간 작품 | 그 작품만 건너뜀 |
| SEARCH_FAILED | 검색 결과 없음 | 그 작품만 건너뜀 |

AUTH_EXPIRED · RATE_LIMITED · PARSING_FAILED 가 하나라도 있으면 런 실패로 본다(cli 종료 코드 1).
"""
import json
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

SUCCESS = 'SUCCESS'
AUTH_EXPIRED = 'AUTH_EXPIRED'
RATE_LIMITED = 'RATE_LIMITED'
PARSING_FAILED = 'PARSING_FAILED'
DETAIL_NOT_FOUND = 'DETAIL_NOT_FOUND'
SEARCH_FAILED = 'SEARCH_FAILED'
FATAL = {AUTH_EXPIRED, RATE_LIMITED, PARSING_FAILED}

_lock = threading.Lock()
_counts: Counter = Counter()
_failures: list[dict] = []


def record(platform: str, status: str, target: str = '', detail: str = '') -> None:
    """platform 의 status 1건을 센다. SUCCESS 가 아니면 대상(URL · 제목 · 구간)과 사유를 남긴다."""
    with _lock:
        _counts[(platform, status)] += 1
        if status != SUCCESS:
            _failures.append({'platform': platform, 'status': status, 'target': target, 'detail': detail[:300],
                              'at': datetime.now(timezone.utc).isoformat(timespec='seconds')})


def reset() -> None:
    with _lock:
        _counts.clear()
        _failures.clear()


def summary() -> dict:
    with _lock:
        by_platform: dict[str, dict[str, int]] = {}
        for (platform, status), n in sorted(_counts.items()):
            by_platform.setdefault(platform, {})[status] = n
        return {'by_platform': by_platform, 'failures': list(_failures)}


def failed() -> bool:
    with _lock:
        return any(status in FATAL for (_, status) in _counts)


def save(output_dir: Path, mode: str) -> Path:
    """output/<날짜>/<mode>_report.json 으로 저장하고 요약을 출력한다."""
    data = {'mode': mode, 'finished_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'failed': failed(), **summary()}
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f'{mode}_report.json'
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n📋 수집 리포트: {path}')
    for platform, counts in data['by_platform'].items():
        print(f'   {platform}: ' + ' · '.join(f'{k} {v}' for k, v in counts.items()))
    if data['failed']:
        fatal = [f for f in data['failures'] if f['status'] in FATAL]
        print(f'❌ 런 실패 — {", ".join(sorted({f["status"] for f in fatal}))}')
        for f in fatal[:10]:
            print(f"   {f['platform']} {f['status']} {f['target']} {f['detail']}")
    return path
