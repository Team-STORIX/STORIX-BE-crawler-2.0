"""
merge_split_works.py — artist_name 표기 차이로 쪼개진 동일 작품 행 병합 (works_name 기준)

같은 works_name 인데 artist_name 표기가 달라 여러 행으로 나뉘고, 설명이 한쪽에만
들어간 경우를 병합한다. description이 '빈' 행을 기준(keeper)으로 유지하고 —
빈 행의 artist_name이 대개 더 온전하므로 — 같은 이름의 '형제행'에서 빈 필드를
COALESCE로 채운 뒤, 형제행의 works_platform/works_hashtag를 keeper로 옮기고 삭제한다.

안전장치:
  - description이 '있는' 형제행이 존재하는 빈 행만 대상.
  - 한 works_name에 keeper(빈 행) 후보가 2개 이상이면 애매하므로 스킵(수동 확인).
  - 기본 DRY-RUN. 실제 반영은 --apply.

    python scripts/merge_split_works.py            # 미리보기
    python scripts/merge_split_works.py --apply     # 반영
"""
import sys
import argparse
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MYSQL_CONFIG
from modules.db_handler import connect_database
from scripts.works_ref_migration import migrate_service_refs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='실제 DB 반영 (기본: dry-run)')
    args = ap.parse_args()

    conn = connect_database(MYSQL_CONFIG)
    if not conn:
        raise SystemExit('DB 연결 실패')
    cur = conn.cursor(dictionary=True)

    cur.execute(
        "SELECT works_id, works_name, artist_name, works_type, thumbnail_url, "
        "age_classification, genre, COALESCE(description,'') AS description FROM works"
    )
    rows = cur.fetchall()

    by_name: dict[str, list] = defaultdict(list)
    for r in rows:
        by_name[r['works_name']].append(r)

    plans = []   # (keeper, [siblings])
    skipped = [] # (works_name, 사유)
    for name, group in by_name.items():
        if len(group) < 2:
            continue
        empties = [r for r in group if not r['description'].strip()]
        if not empties:
            continue
        if len(empties) > 1:
            skipped.append((name, f'빈 행이 {len(empties)}개 — 수동 확인'))
            continue
        keeper = empties[0]
        # 같은 works_type(예: 웹툰)인 형제행만 병합 대상. 웹소설 등 다른 타입은 별개 작품으로 유지.
        siblings = [
            r for r in group
            if r['works_id'] != keeper['works_id'] and r['works_type'] == keeper['works_type']
        ]
        if not any(s['description'].strip() for s in siblings):
            skipped.append((name, f'동일 타입({keeper["works_type"]}) 형제행에 설명 없음 — 재크롤 필요'))
            continue
        plans.append((keeper, siblings))

    print(f"\n{'='*70}")
    print(f"병합 대상(빈 행 + 설명 형제행): {len(plans)}건   |   스킵(수동): {len(skipped)}건")
    print(f"{'='*70}")
    for keeper, sibs in plans:
        best = max(sibs, key=lambda s: len(s['description']))
        print(f"\n  KEEP #{keeper['works_id']} [{keeper['works_type']}] "
              f"{keeper['works_name'][:26]} — artist={keeper['artist_name']!r}")
        for s in sibs:
            print(f"     흡수/삭제 #{s['works_id']} artist={s['artist_name']!r} "
                  f"(desc {len(s['description'])}자)")
        print(f"     → 채울 description: #{best['works_id']} 의 {len(best['description'])}자")
    for name, why in skipped:
        print(f"  ⏭️  {name[:30]} — {why}")

    if not args.apply:
        print(f"\n💡 DRY-RUN. 반영하려면: python scripts/merge_split_works.py --apply")
        conn.close()
        return

    wcur = conn.cursor()
    for keeper, sibs in plans:
        kid = keeper['works_id']
        best = max(sibs, key=lambda s: len(s['description']))
        # keeper의 빈 필드를 형제행 값으로 채움 (description은 가장 긴 것)
        wcur.execute(
            "UPDATE works SET "
            "description=COALESCE(NULLIF(description,''), %s), "
            "thumbnail_url=COALESCE(NULLIF(thumbnail_url,''), %s), "
            "age_classification=COALESCE(NULLIF(age_classification,''), %s), "
            "genre=COALESCE(NULLIF(genre,''), %s) WHERE works_id=%s",
            (best['description'], best['thumbnail_url'],
             best['age_classification'], best['genre'], kid),
        )
        for s in sibs:
            sid = s['works_id']
            # 서비스 테이블(topic_room, 즐겨찾기 등) 참조를 먼저 keeper 로 이전.
            # 충돌 잔여가 있으면 고아 참조(API NPE)를 만들지 않도록 이 형제행은 삭제 스킵.
            if not migrate_service_refs(wcur, kid, sid):
                continue
            wcur.execute("INSERT IGNORE INTO works_platform (works_id, platform) "
                         "SELECT %s, platform FROM works_platform WHERE works_id=%s", (kid, sid))
            wcur.execute("INSERT IGNORE INTO works_hashtag (works_id, hashtag_id) "
                         "SELECT %s, hashtag_id FROM works_hashtag WHERE works_id=%s", (kid, sid))
            wcur.execute("DELETE FROM works_platform WHERE works_id=%s", (sid,))
            wcur.execute("DELETE FROM works_hashtag WHERE works_id=%s", (sid,))
            wcur.execute("DELETE FROM works WHERE works_id=%s", (sid,))

    conn.commit()
    print(f"\n✅ 병합 완료 — {len(plans)}건")
    conn.close()


if __name__ == '__main__':
    main()
