"""검수 흐름. API(review/app.py)와 CLI(cli.py review)가 같이 쓴다."""
import json
import os
from datetime import datetime
from pathlib import Path

from review.catalog import EnumCatalog
from review.rules import AUTO_PASS, ENUM_FIELDS, REJECTED, check_run, validate_item
from review.store import APPROVED, MAX_SOURCE_FAILS, StagingStore


# Layer 3 (#70): 같은 (필드, 원문) 판정이 이만큼 모두 같은 결론이면 매핑으로 승격한다
PROMOTE_MIN = int(os.getenv('REVIEW_PROMOTE_MIN') or 3)


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
    """판정 전에 쌓인 사람 판정을 매핑으로 승격해(Layer 3) Layer 1 이 쓰게 한다."""
    store.promote_mappings(PROMOTE_MIN)
    mappings = store.mappings()
    verdicts = [validate_item(it, catalog, mappings) for it in items]
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
    verdict = validate_item(item, catalog, store.mappings())
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


# 바깥 검수 제안 (#68)
# Layer 1 이 못 정한 검수 대기 행에 레포 밖 도구(로컬 claude -p 등)가 낸 제안을 붙인다.
# 제안은 llm_verdict 에 남기기만 하고, 사람이 apply_suggestions 를 돌릴 때 approve · reject 로 반영한다.
#   {"staging_id": 12, "action": "approve", "overrides": {"genre": "판타지"}, "confidence": 0.9, "reason": "..."}
#   action: approve(고친 값으로 승인) | reject(거절) | skip(사람이 직접 봄)

SUGGEST_ACTIONS = ('approve', 'reject', 'skip')
# 제안으로 고칠 수 있는 값. 작품을 특정하는 값(작품명 · 플랫폼 · 링크)은 고치지 않는다
SUGGEST_FIELDS = ('artist_name', 'author', 'illustrator', 'original_author', 'description', 'thumbnail_url',
                  'genre', 'age_classification', 'works_type', 'hashtags')


def parse_suggestion(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ReviewError('제안은 객체여야 함')
    try:
        staging_id = int(raw['staging_id'])
    except (KeyError, TypeError, ValueError):
        raise ReviewError(f'staging_id 가 없거나 숫자가 아님: {raw.get("staging_id")!r}')
    action = raw.get('action')
    if action not in SUGGEST_ACTIONS:
        raise ReviewError(f'#{staging_id} action 은 {" | ".join(SUGGEST_ACTIONS)} 중 하나: {action!r}')
    overrides = raw.get('overrides') or {}
    if not isinstance(overrides, dict):
        raise ReviewError(f'#{staging_id} overrides 는 객체여야 함')
    unknown = sorted(set(overrides) - set(SUGGEST_FIELDS))
    if unknown:
        raise ReviewError(f'#{staging_id} 고칠 수 없는 값: {", ".join(unknown)}')
    confidence = raw.get('confidence')
    if confidence is not None:
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            raise ReviewError(f'#{staging_id} confidence 는 숫자: {confidence!r}')
        if not 0 <= confidence <= 1:
            raise ReviewError(f'#{staging_id} confidence 는 0~1: {confidence}')
    try:
        target = int(raw['target_works_id']) if raw.get('target_works_id') else None
    except (TypeError, ValueError):
        raise ReviewError(f'#{staging_id} target_works_id 는 숫자: {raw.get("target_works_id")!r}')
    return {
        'staging_id': staging_id,
        'confidence': confidence,
        'verdict': {
            'action': action,
            'overrides': overrides,
            'target_works_id': target,
            'create_new': bool(raw.get('create_new')),
            'reason': str(raw.get('reason') or '')[:1000],
        },
    }


# 바깥 검수에 예시로 싣는 이전 판정 수 (#10)
EXAMPLE_LIMIT = int(os.getenv('REVIEW_EXAMPLE_LIMIT') or 30)


def _decision_examples(store: StagingStore, catalog: EnumCatalog) -> list[dict]:
    """승격 못 한 판정을 제안 형식(dbValue)으로. 판정 기록은 enum 을 name 으로 남기지만 제안은 dbValue 로 받는다."""
    db_values = {kind: {v['name']: v.get('dbValue') or v['name'] for v in catalog.raw[kind]['values']}
                 for kind, _ in ENUM_FIELDS.values()}
    examples = []
    for e in store.decision_examples(EXAMPLE_LIMIT):
        kind = ENUM_FIELDS.get(e['field'], (None,))[0]
        decided = db_values[kind].get(e['decided_value'], e['decided_value']) if kind else e['decided_value']
        examples.append({**e, 'decided_value': decided})
    return examples


def review_export(store: StagingStore, catalog: EnumCatalog, run_ids: list[str] | None, limit: int | None = None) -> dict:
    """검수 대기 행을 바깥 검수에 넘길 형태로. run_ids 가 None 이면 전체 (#72).
    고를 수 있는 enum 값과 제안 형식, 아직 매핑으로 승격 못 한 이전 판정(few-shot, #10)을 같이 싣는다."""
    rows = []
    for r in store.review_rows(run_ids, limit):
        raw = r.get('raw') or {}
        rows.append({
            'staging_id': r['id'],
            'source_url': r['source_url'],
            **{f: raw.get(f) for f in ('platform', 'works_name', 'artist_name', 'author', 'illustrator',
                                       'original_author', 'works_type', 'genre', 'age_classification',
                                       'thumbnail_url', 'hashtags')},
            'description': (raw.get('description') or '')[:1500],
            'violations': [v for v in (r.get('violations') or []) if v.get('severity') != 'INFO'],
        })
    return {
        # 장르 · 연령 · 작품 유형은 이 값(dbValue) 중에서 고른다
        'allowed_values': {kind: [v.get('dbValue') or v['name'] for v in catalog.raw[kind]['values']]
                           for kind in ('genre', 'ageClassification', 'worksType')},
        'suggestion_format': {
            'staging_id': 'rows[].staging_id',
            'action': ' | '.join(SUGGEST_ACTIONS),
            'overrides': f'고칠 값만. 가능한 키: {", ".join(SUGGEST_FIELDS)}',
            'confidence': '0~1',
            'reason': '판단 근거',
        },
        # 사람이 이전에 같은 원문을 이렇게 정했다. 같은 원문이면 이 결론을 따른다 (by_human=false 는 제안을 승인한 것)
        'examples': _decision_examples(store, catalog),
        'rows': rows,
    }


def save_suggestions(store: StagingStore, suggestions: list[dict], by: str) -> dict:
    """하나라도 형식이 틀리면 아무것도 남기지 않는다."""
    parsed = [parse_suggestion(s) for s in suggestions]
    saved, missed = 0, []
    for p in parsed:
        if store.save_suggestion(p['staging_id'], {**p['verdict'], 'by': by}, p['confidence']):
            saved += 1
        else:
            missed.append(p['staging_id'])  # 없는 행이거나 이미 검수 대기가 아님
    return {'saved': saved, 'missed': missed}


def apply_suggestions(store: StagingStore, catalog: EnumCatalog, run_ids: list[str] | None) -> dict:
    """검수 대기 행 중 제안이 붙은 것을 반영한다. run_ids 가 None 이면 전체. 사람이 실행해야 한다 (requests --resume · stage apply).
    승인은 approve 를 그대로 타서 고친 값이 Layer 1 을 다시 통과해야 하고, 판정은 review_decision 에 남는다.
    reviewer 는 'suggest:<제안 낸 도구>' 로 남겨 사람이 직접 판정한 것과 구분한다."""
    summary: dict = {'approved': 0, 'rejected': 0, 'skipped': 0, 'failed': [], 'approved_ids': []}
    for row in store.review_rows(run_ids):
        verdict = row.get('llm_verdict')
        if not verdict:
            continue
        reviewer = f"suggest:{verdict.get('by') or 'unknown'}"[:100]
        action = verdict.get('action')
        try:
            if action == 'approve':
                approve(store, catalog, row['id'], verdict.get('overrides') or {}, reviewer,
                        verdict.get('target_works_id'), bool(verdict.get('create_new')))
                summary['approved'] += 1
                summary['approved_ids'].append(row['id'])
            elif action == 'reject':
                reject(store, row['id'], reviewer, verdict.get('reason') or '바깥 검수에서 거절')
                summary['rejected'] += 1
            else:
                summary['skipped'] += 1
        except ReviewError as e:
            # 고친 값이 아직 Layer 1 을 못 넘으면 검수 대기로 남는다
            codes = ', '.join(v.get('code') or '' for v in e.violations)
            summary['failed'].append({'id': row['id'], 'works_name': row.get('works_name'),
                                      'error': f'{e}{f" ({codes})" if codes else ""}'})
    return summary


def reject(store: StagingStore, staging_id: int, reviewer: str, reason: str) -> dict:
    row = store.get(staging_id)
    if not row:
        raise ReviewError('staging 항목 없음')
    violations = (row['violations'] or []) + [
        {'field': None, 'code': 'REVIEWER_REJECTED', 'value': reason, 'severity': REJECTED}
    ]
    store.save_review(staging_id, REJECTED, row['normalized'] or {}, violations, reviewer, [])
    return {'id': staging_id, 'status': REJECTED}
