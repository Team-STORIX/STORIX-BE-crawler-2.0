"""
diagnose_empty_desc.py — description이 빈 works 행의 원인 분류 (읽기 전용)

같은 작품(works_name)이 artist_name 표기 차이로 여러 행으로 쪼개지면서,
설명은 새 행에 들어가고 옛 표기의 빈 행이 남는 문제를 진단한다.

각 빈 행을 분류:
  [MERGEABLE]   같은 works_name 에 설명이 있는 형제행 존재 → works_name 기준 병합으로 복구 가능
  [NEEDS_CRAWL] 어떤 행에도 설명이 없음 → 한 번도 제대로 안 긁힘, 재크롤 필요

DB를 변경하지 않는다. 규모 파악용.
    python scripts/diagnose_empty_desc.py
    python scripts/diagnose_empty_desc.py --show 40   # 샘플 개수
"""
import sys
import argparse
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MYSQL_CONFIG
from modules.db_handler import connect_database


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--show', type=int, default=25, help='분류별 샘플 출력 개수')
    args = ap.parse_args()

    conn = connect_database(MYSQL_CONFIG)
    if not conn:
        raise SystemExit('DB 연결 실패')
    cur = conn.cursor(dictionary=True)

    cur.execute(
        "SELECT works_id, works_name, artist_name, works_type, "
        "COALESCE(description,'') AS description FROM works"
    )
    rows = cur.fetchall()

    # works_name 별로 '설명 있는 행'이 하나라도 있는지
    has_desc_by_name: dict[str, bool] = defaultdict(bool)
    for r in rows:
        if r['description'].strip():
            has_desc_by_name[r['works_name']] = True

    empty_rows = [r for r in rows if not r['description'].strip()]
    mergeable, needs_crawl = [], []
    for r in empty_rows:
        (mergeable if has_desc_by_name.get(r['works_name']) else needs_crawl).append(r)

    total = len(rows)
    print(f"\n{'='*70}")
    print(f"전체 works 행: {total}  |  description 빈 행: {len(empty_rows)}")
    print(f"  ├─ [MERGEABLE]   설명 있는 형제행 존재 (병합 복구 가능): {len(mergeable)}")
    print(f"  └─ [NEEDS_CRAWL] 형제행도 설명 없음 (재크롤 필요)     : {len(needs_crawl)}")
    print(f"{'='*70}")

    print(f"\n[MERGEABLE] 샘플 {min(args.show, len(mergeable))}건")
    for r in mergeable[:args.show]:
        # 같은 이름의 설명 있는 형제행 예시
        sib = next((x for x in rows
                    if x['works_name'] == r['works_name'] and x['description'].strip()), None)
        sd = len(sib['description']) if sib else 0
        print(f"  #{r['works_id']} [{r['works_type']}] {r['works_name'][:24]:24} "
              f"artist={r['artist_name']!r}")
        if sib:
            print(f"        ↔ 형제 #{sib['works_id']} artist={sib['artist_name']!r} (desc {sd}자)")

    print(f"\n[NEEDS_CRAWL] 샘플 {min(args.show, len(needs_crawl))}건")
    for r in needs_crawl[:args.show]:
        print(f"  #{r['works_id']} [{r['works_type']}] {r['works_name'][:30]:30} "
              f"artist={r['artist_name']!r}")

    conn.close()


if __name__ == '__main__':
    main()
