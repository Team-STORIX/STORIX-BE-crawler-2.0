import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from config import OUTPUT_DIR

log = logging.getLogger(__name__)

STATUS_FILE = OUTPUT_DIR / 'platform_status.jsonl'
_write_lock = threading.Lock()


@dataclass
class RunRecord:
    platform: str
    mode: str
    started_at: str
    finished_at: str = ''
    status: str = 'running'
    count: int = 0
    error: str = ''


def start_run(platform: str, mode: str) -> RunRecord:
    rec = RunRecord(platform=platform, mode=mode, started_at=_now())
    _append(rec)
    log.info('[status] ▶ 시작: %s / %s', platform, mode)
    return rec


def finish_run(rec: RunRecord, count: int = 0, error: str = '') -> None:
    rec.finished_at = _now()
    rec.count = count
    if error:
        rec.status = 'failed'
        rec.error = error
        log.error('[status] ✗ 실패: %s / %s — %s', rec.platform, rec.mode, error)
    else:
        rec.status = 'success'
        log.info('[status] ✓ 완료: %s / %s — %d건', rec.platform, rec.mode, count)
    _append(rec)


def get_recent(platform: str = '', limit: int = 20) -> list[dict]:
    if not STATUS_FILE.exists():
        return []

    records: list[dict] = []
    with open(STATUS_FILE, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if platform and rec.get('platform') != platform:
                continue
            records.append(rec)

    finished = [r for r in records if r.get('status') != 'running']
    return finished[-limit:]


def summary() -> list[dict]:
    all_records = get_recent(limit=500)
    groups: dict[str, list[dict]] = {}
    for r in all_records:
        key = f"{r['platform']}/{r['mode']}"
        groups.setdefault(key, []).append(r)

    result = []
    for key, records in sorted(groups.items()):
        total = len(records)
        success = sum(1 for r in records if r.get('status') == 'success')
        last = records[-1]
        result.append({
            'key': key,
            'last_status': last.get('status'),
            'last_count': last.get('count', 0),
            'last_finished': last.get('finished_at', ''),
            'success_rate': f'{success}/{total}',
        })
    return result


def print_summary() -> None:
    rows = summary()
    if not rows:
        print('(실행 이력 없음)')
        return

    header = f"{'플랫폼/모드':<35} {'마지막 상태':<10} {'건수':>6}  {'완료 시각':<20} {'성공률'}"
    print(header)
    print('-' * len(header))
    for r in rows:
        icon = '✓' if r['last_status'] == 'success' else '✗'
        print(
            f"{r['key']:<35} {icon} {r['last_status']:<8} "
            f"{r['last_count']:>6}건  {r['last_finished']:<20} {r['success_rate']}"
        )


def _now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _append(rec: RunRecord) -> None:
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(asdict(rec), ensure_ascii=False)
    with _write_lock:
        with open(STATUS_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
