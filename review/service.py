"""검수 흐름. API(review/app.py)와 CLI(cli.py review)가 같이 쓴다."""
import json
from pathlib import Path

from review.catalog import EnumCatalog
from review.rules import AUTO_PASS, ENUM_FIELDS, REJECTED, check_run, validate_item
from review.store import APPROVED, StagingStore


class ReviewError(Exception):
    def __init__(self, message: str, violations: list[dict] | None = None):
        super().__init__(message)
        self.violations = violations or []


def read_jsonl(path: Path) -> list[dict]:
    items = []
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return items


def load_run(store: StagingStore, catalog: EnumCatalog, items: list[dict],
             source: str, source_file: str | None = None) -> dict:
    """크롤 산출물 하나를 staging 에 넣고 Layer 1 · 1.5 판정까지 한다."""
    verdicts = [validate_item(it, catalog) for it in items]
    hold_reasons = check_run(items, store.previous_count(source))
    run_id = store.save_run(source, source_file, items, verdicts, hold_reasons)

    counts: dict[str, int] = {}
    for v in verdicts:
        counts[v.status] = counts.get(v.status, 0) + 1
    return {'run_id': run_id, 'items': len(items), 'by_status': counts,
            'held': bool(hold_reasons), 'hold_reasons': hold_reasons}


def approve(store: StagingStore, catalog: EnumCatalog, staging_id: int,
            overrides: dict, reviewer: str) -> dict:
    """사람이 값을 고쳐 승인한다. 고친 값으로 Layer 1 을 다시 돌려 통과해야 APPROVED."""
    row = store.get(staging_id)
    if not row:
        raise ReviewError('staging 항목 없음')

    item = {**row['raw'], **{k: v for k, v in overrides.items() if v is not None}}
    verdict = validate_item(item, catalog)
    if verdict.status != AUTO_PASS:
        raise ReviewError('고친 값이 아직 검증을 통과하지 못함', verdict.violations)

    # enum 판정만 학습 재료로 남긴다. 같은 원문 → 같은 결론이 쌓이면 Layer 3 에서 매핑으로 승격
    decisions = [
        (f, (row['raw'].get(f) or None), verdict.normalized[f])
        for f in ENUM_FIELDS
        if f in overrides and catalog.resolve(ENUM_FIELDS[f][0], row['raw'].get(f)) != verdict.normalized[f]
    ]
    store.save_review(staging_id, APPROVED, verdict.normalized, [], reviewer, decisions)
    return {'id': staging_id, 'status': APPROVED, 'normalized': verdict.normalized}


def reject(store: StagingStore, staging_id: int, reviewer: str, reason: str) -> dict:
    row = store.get(staging_id)
    if not row:
        raise ReviewError('staging 항목 없음')
    violations = (row['violations'] or []) + [
        {'field': None, 'code': 'REVIEWER_REJECTED', 'value': reason, 'severity': REJECTED}
    ]
    store.save_review(staging_id, REJECTED, row['normalized'] or {}, violations, reviewer, [])
    return {'id': staging_id, 'status': REJECTED}
