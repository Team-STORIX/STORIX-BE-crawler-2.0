"""검수 흐름. API(review/app.py)와 CLI(cli.py review)가 같이 쓴다."""
import json
from datetime import datetime
from pathlib import Path

from review.catalog import EnumCatalog
from review.rules import AUTO_PASS, ENUM_FIELDS, REJECTED, check_run, validate_item
from review.store import APPROVED, MAX_SOURCE_FAILS, StagingStore


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


# 수집 이력에 실패로 남기는 리포트 상태. 검색 실패(SEARCH_FAILED)는 대상이 제목이라 링크가 없다
_SOURCE_FAILURES = ('DETAIL_NOT_FOUND',)


def report_for(jsonl_path: Path) -> Path | None:
    """수집 결과 파일과 같은 런의 수집 리포트. 'ridibooks_search_titles.jsonl' → 'search_titles_report.json'."""
    for report in sorted(jsonl_path.parent.glob('*_report.json')):
        mode = report.name[:-len('_report.json')]
        if jsonl_path.stem == mode or jsonl_path.stem.endswith(f'_{mode}'):
            return report
    return None


def record_report_failures(store: StagingStore, report_path: Path) -> dict:
    """수집 리포트의 상세 수집 실패를 작품별 수집 이력(works_source)에 남긴다 (#28).
    같은 리포트를 여러 번 읽어도 실패는 한 번만 센다 (store.record_source_failure)."""
    data = json.loads(Path(report_path).read_text(encoding='utf-8'))
    recorded = broken = 0
    for f in data.get('failures') or []:
        target = f.get('target') or ''
        if f.get('status') not in _SOURCE_FAILURES or not target.startswith('http'):
            continue
        at = datetime.fromisoformat(f['at']) if f.get('at') else None
        fails = store.record_source_failure(target, f['status'], f.get('detail') or '', at)
        if fails is not None:
            recorded += 1
            broken += fails >= MAX_SOURCE_FAILS
    return {'failures': recorded, 'link_broken': broken}


def approve(store: StagingStore, catalog: EnumCatalog, staging_id: int,
            overrides: dict, reviewer: str,
            target_works_id: int | None = None, create_new: bool = False) -> dict:
    """사람이 값을 고쳐 승인한다. 고친 값으로 Layer 1 을 다시 돌려 통과해야 APPROVED.

    BE 가 중복 의심(SUSPECTED_DUPLICATE)으로 돌려보낸 건은 사람이 고른다
      target_works_id: 그 기존 작품에 붙인다 (BE 판정 생략)
      create_new:      다른 작품이 맞으니 새로 만든다
    """
    if target_works_id and create_new:
        raise ReviewError('target_works_id 와 create_new 는 함께 쓸 수 없음')
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
    normalized = dict(verdict.normalized)
    if target_works_id:
        normalized['_target_works_id'] = int(target_works_id)
    if create_new:
        normalized['_create_new'] = True
    store.save_review(staging_id, APPROVED, normalized, [], reviewer, decisions)
    return {'id': staging_id, 'status': APPROVED, 'normalized': normalized}


def reject(store: StagingStore, staging_id: int, reviewer: str, reason: str) -> dict:
    row = store.get(staging_id)
    if not row:
        raise ReviewError('staging 항목 없음')
    violations = (row['violations'] or []) + [
        {'field': None, 'code': 'REVIEWER_REJECTED', 'value': reason, 'severity': REJECTED}
    ]
    store.save_review(staging_id, REJECTED, row['normalized'] or {}, violations, reviewer, [])
    return {'id': staging_id, 'status': REJECTED}
