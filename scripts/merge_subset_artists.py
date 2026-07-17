"""
merge_subset_artists.py — 작가 목록 중복/부분집합 중복 행 정리

배경:
    구버전 네이버 웹툰 크롤러는 첫 번째 작가만 수집해서(예: '손차양'),
    글/그림 작가가 다른 작품을 재크롤하면 전체 작가('손차양, 냥지')로
    artist_name 이 달라져 upsert 키(works_name+artist_name)가 어긋나
    중복 행이 생긴다. 일부 행은 artist_name 안에 같은 이름이 중복
    ('자작, 자작')된 오염도 있다.

3단계 정리:
    1) 이름 중복 제거: artist_name/author/illustrator/original_author 의
       콤마 엔트리 중복을 순서 보존 제거 ('자작, 자작' → '자작')
    2) 동일 집합 병합: 같은 works_name+works_type 에서 (중복 제거 후)
       작가 집합이 완전히 같은 행들 → 설명 긴 행(동률이면 works_id 낮은 행)을
       keeper 로 병합
    3) 부분집합 병합: 작가 집합이 다른 행의 '진부분집합'인 행
       ({손차양} ⊂ {손차양, 냥지}) → 초집합 행을 keeper 로 병합
       (초집합 후보가 2개 이상이면 스킵, 수동 확인)

병합 = keeper 빈 필드 COALESCE 채움 + works_platform/works_hashtag 이전 + 행 삭제.

기본은 DRY-RUN(변경 없음). 실제 적용은 --apply.
    python scripts/merge_subset_artists.py           # 미리보기
    python scripts/merge_subset_artists.py --apply    # 실제 반영
"""
import sys
import argparse
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MYSQL_CONFIG
from modules.db_handler import connect_database, GENRE_PRIORITY, _ROLE_TOKEN_RE
from scripts.works_ref_migration import migrate_service_refs


def _strip_role_labels(entry: str) -> str:
    """단일 작가 엔트리에서 앞뒤 역할 라벨(글/그림/작화/원작/각색)을 제거.

    '작화 스푼' → '스푼', '글 홍길동' → '홍길동'.
    '글쓰는기계'(토큰 전체가 역할이 아님)는 그대로 둔다.
    """
    tokens = entry.split()
    while tokens and _ROLE_TOKEN_RE.match(tokens[0]):
        tokens.pop(0)
    while tokens and _ROLE_TOKEN_RE.match(tokens[-1]):
        tokens.pop()
    return ' '.join(tokens).strip()


def dedupe_entries(text: str) -> str:
    """콤마 엔트리별 역할 라벨 제거 + 중복 제거 (순서 보존).

    '플루토스, 작화 스푼' → '플루토스, 스푼'  (→ '플루토스, 스푼' 행과 병합 가능)
    """
    seen, out = set(), []
    for p in (text or '').split(','):
        p = _strip_role_labels(p.strip())
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return ', '.join(out)


def artist_set(artist_name: str) -> frozenset[str]:
    return frozenset(p.strip() for p in (artist_name or '').split(',') if p.strip())


def pick_keeper(rows: list[dict]) -> dict:
    """설명이 가장 긴 행, 동률이면 works_id 낮은 행."""
    return max(rows, key=lambda r: (len(r['description'] or ''), -r['works_id']))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='실제 DB에 반영 (기본: dry-run)')
    args = ap.parse_args()

    conn = connect_database(MYSQL_CONFIG)
    if not conn:
        raise SystemExit('DB 연결 실패')
    cur = conn.cursor(dictionary=True)

    cur.execute(
        "SELECT works_id, works_name, artist_name, author, illustrator, "
        "original_author, works_type, description, thumbnail_url, "
        "age_classification, genre FROM works"
    )
    rows = cur.fetchall()

    # 1) 이름 중복 제거 대상
    dedupes = []  # (row, {field: new_value})
    for r in rows:
        changes = {}
        for field in ('artist_name', 'author', 'illustrator', 'original_author'):
            val = r[field]
            if val and dedupe_entries(val) != val.strip():
                changes[field] = dedupe_entries(val)
        if changes:
            dedupes.append((r, changes))
            if 'artist_name' in changes:
                r['artist_name'] = changes['artist_name']  # 이후 단계는 정리된 값 기준

    # 그룹핑 (중복 제거 반영된 artist_name 기준)
    groups: dict[tuple, list] = defaultdict(list)
    for r in rows:
        groups[(r['works_name'], r['works_type'])].append(r)

    equal_merges, subset_merges, skipped = [], [], []
    survivors_by_group: dict[tuple, dict[frozenset, dict]] = {}

    # 2) 동일 집합 병합
    for key, group in groups.items():
        by_set: dict[frozenset, list] = defaultdict(list)
        for r in group:
            s = artist_set(r['artist_name'])
            if s:
                by_set[s].append(r)
        keepers: dict[frozenset, dict] = {}
        for s, same in by_set.items():
            keeper = pick_keeper(same)
            keepers[s] = keeper
            for r in same:
                if r['works_id'] != keeper['works_id']:
                    equal_merges.append((r, keeper))
        survivors_by_group[key] = keepers

    # 3) 부분집합 병합 (동일 집합 병합 후 남은 대표끼리 비교)
    for key, keepers in survivors_by_group.items():
        for s, sub in keepers.items():
            supersets = [keepers[f] for f in keepers if s < f]
            if not supersets:
                continue
            if len(supersets) > 1:
                skipped.append((sub, supersets))
                continue
            subset_merges.append((sub, supersets[0]))

    print(f"\n{'='*70}")
    print(f"이름 중복 정리 {len(dedupes)}건 | 동일 집합 병합 {len(equal_merges)}건 | "
          f"부분집합 병합 {len(subset_merges)}건 | 스킵 {len(skipped)}건")
    print(f"{'='*70}")

    print(f"\n[1) 이름 중복 제거] {len(dedupes)}건")
    for r, changes in dedupes:
        print(f"  #{r['works_id']} {r['works_name'][:22]:22} "
              + ' | '.join(f"{f} → {v!r}" for f, v in changes.items()))

    print(f"\n[2) 동일 집합 병합] {len(equal_merges)}건")
    for sub, keep in equal_merges:
        print(f"  #{sub['works_id']} {sub['works_name'][:22]:22} "
              f"{sub['artist_name']!r} → keeper #{keep['works_id']}")

    print(f"\n[3) 부분집합 병합] {len(subset_merges)}건")
    for sub, keep in subset_merges:
        print(f"  #{sub['works_id']} {sub['works_name'][:22]:22} "
              f"{sub['artist_name']!r} → keeper #{keep['works_id']} {keep['artist_name']!r}")

    for sub, sups in skipped:
        cands = ', '.join(f"#{r['works_id']} {r['artist_name']!r}" for r in sups)
        print(f"  ⏭️  #{sub['works_id']} {sub['works_name'][:22]} {sub['artist_name']!r} "
              f"— 초집합 후보 다수: {cands}")

    if not args.apply:
        print(f"\n💡 DRY-RUN 입니다. 실제 반영하려면: python scripts/merge_subset_artists.py --apply")
        conn.close()
        return

    wcur = conn.cursor()

    # 1) 이름 중복 제거 (병합 전에 실행해야 keeper 값이 깨끗함)
    for r, changes in dedupes:
        sets = ', '.join(f"{f}=%s" for f in changes)
        wcur.execute(
            f"UPDATE works SET {sets} WHERE works_id=%s",
            (*changes.values(), r['works_id']),
        )

    # 2) + 3) 병합 (동일 집합 → 부분집합 순서: 링크가 최종 keeper 로 흘러가도록)
    def merge(sub: dict, keep: dict) -> None:
        keep_id, drop_id = keep['works_id'], sub['works_id']
        # 서비스 테이블(topic_room, 즐겨찾기 등)의 참조를 먼저 keeper 로 이전.
        # 유니크 충돌이 남으면 고아 참조(API NPE)를 만들지 않도록 이 병합은 스킵.
        if not migrate_service_refs(wcur, keep_id, drop_id):
            return
        # 장르는 우선순위 높은 쪽 유지 (예: 구행 '로판'(5) > 신행 '판타지'(1) → 로판)
        sub_genre = (sub['genre'] or '').strip()
        keep_genre = (keep['genre'] or '').strip()
        genre = sub_genre if (
            sub_genre and GENRE_PRIORITY.get(sub_genre, 0) > GENRE_PRIORITY.get(keep_genre, 0)
        ) else keep_genre or sub_genre
        wcur.execute(
            "UPDATE works SET "
            "description=COALESCE(NULLIF(description,''), %s), "
            "thumbnail_url=COALESCE(NULLIF(thumbnail_url,''), %s), "
            "age_classification=COALESCE(NULLIF(age_classification,''), %s), "
            "genre=%s WHERE works_id=%s",
            (sub['description'], sub['thumbnail_url'],
             sub['age_classification'], genre, keep_id),
        )
        wcur.execute("INSERT IGNORE INTO works_platform (works_id, platform) "
                     "SELECT %s, platform FROM works_platform WHERE works_id=%s",
                     (keep_id, drop_id))
        wcur.execute("INSERT IGNORE INTO works_hashtag (works_id, hashtag_id) "
                     "SELECT %s, hashtag_id FROM works_hashtag WHERE works_id=%s",
                     (keep_id, drop_id))
        wcur.execute("DELETE FROM works_platform WHERE works_id=%s", (drop_id,))
        wcur.execute("DELETE FROM works_hashtag WHERE works_id=%s", (drop_id,))
        wcur.execute("DELETE FROM works WHERE works_id=%s", (drop_id,))

    for sub, keep in equal_merges:
        merge(sub, keep)
    for sub, keep in subset_merges:
        merge(sub, keep)

    conn.commit()
    print(f"\n✅ 적용 완료 — 이름정리 {len(dedupes)}건, 동일병합 {len(equal_merges)}건, "
          f"부분집합병합 {len(subset_merges)}건")
    conn.close()


if __name__ == '__main__':
    main()
