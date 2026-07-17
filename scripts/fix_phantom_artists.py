"""
fix_phantom_artists.py — parse_artists 버그로 오염된 artist_name 교정 마이그레이션

배경:
    구버전 parse_artists()는 '글쓰는기계'처럼 이름에 '글/그림/원작/각색'이
    substring으로 들어가면 앞 글자를 역할 라벨로 오인해 떼어냈다. 그 결과
    artist_name이 '글쓰는기계, 쓰는기계'(원본 + '글'뗀 팬텀)로 오염되어
    이후 upsert의 매칭 키(works_name+artist_name)가 어긋나고 중복 행이 생겼다.

판별 규칙 (정상 다중작가와 구분):
    artist_name을 ', '로 나눈 엔트리 중, 어떤 엔트리 q에 대해 '글'+q 가
    같은 목록에 존재하면 q는 '팬텀'이다.  예) '글작소, 작소' → '작소'가 팬텀.
    '글작소, 진설'(진설은 글진설 없음)이나 '글쓰는기계, 펭스, KZV'는 손대지 않는다.

동작:
    - 팬텀 제거한 corrected_artist 계산 후, FIXED parse_artists로 author/illustrator/
      original 재산출.
    - 같은 works_name + corrected_artist 행(clean twin)이 이미 있으면 → 병합
      (twin의 빈 필드를 이 행 값으로 COALESCE 채우고, works_platform/works_hashtag를
       twin으로 이전 후 오염 행 삭제).
    - 없으면 → 오염 행의 artist_name/author/illustrator/original 만 교정(UPDATE).

기본은 DRY-RUN(변경 없음). 실제 적용은 --apply.
    python scripts/fix_phantom_artists.py           # 미리보기
    python scripts/fix_phantom_artists.py --apply    # 실제 반영
"""
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MYSQL_CONFIG
from modules.db_handler import connect_database, parse_artists, _canonical_artist_name
from scripts.works_ref_migration import migrate_service_refs


def find_phantoms(artist_name: str) -> list[str]:
    """artist_name에서 팬텀 엔트리 목록을 반환 ('글'+q 가 같이 있는 q)."""
    parts = [p.strip() for p in artist_name.split(',') if p.strip()]
    pset = set(parts)
    return [q for q in parts if ('글' + q) in pset and ('글' + q) != q]


def corrected_fields(artist_name: str) -> dict | None:
    """오염된 artist_name → 교정된 필드들. 팬텀이 없으면 None."""
    phantoms = find_phantoms(artist_name)
    if not phantoms:
        return None
    parts = [p.strip() for p in artist_name.split(',') if p.strip()]
    kept = [p for p in parts if p not in phantoms]
    corrected_raw = ', '.join(kept)

    author, illustrator, original = parse_artists(corrected_raw)
    canonical = _canonical_artist_name(corrected_raw, author, illustrator, original)
    return {
        'artist_name': canonical,
        'author': author,
        'illustrator': illustrator,
        'original_author': original,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='실제 DB에 반영 (기본: dry-run)')
    args = ap.parse_args()

    conn = connect_database(MYSQL_CONFIG)
    if not conn:
        raise SystemExit('DB 연결 실패')
    cur = conn.cursor(dictionary=True)

    # 후보: artist_name에 '글'과 콤마가 모두 있는 행 (파이썬에서 정밀 판별)
    cur.execute(
        "SELECT works_id, works_name, artist_name, author, illustrator, "
        "original_author, description, thumbnail_url, age_classification, "
        "genre, works_type FROM works WHERE artist_name LIKE '%글%' "
        "AND artist_name LIKE '%,%'"
    )
    rows = cur.fetchall()

    renames, merges = [], []
    for r in rows:
        fix = corrected_fields(r['artist_name'] or '')
        if not fix:
            continue  # 정상 다중작가 → 스킵
        # clean twin 존재 여부
        cur.execute(
            "SELECT works_id, description, thumbnail_url, age_classification, "
            "genre FROM works WHERE works_name=%s AND artist_name=%s AND works_id<>%s",
            (r['works_name'], fix['artist_name'], r['works_id']),
        )
        twin = cur.fetchone()
        (merges if twin else renames).append((r, fix, twin))

    print(f"\n{'='*70}")
    print(f"오염(팬텀) 행: {len(renames)+len(merges)}건  |  이름교정 {len(renames)} · 병합 {len(merges)}")
    print(f"{'='*70}")

    print(f"\n[이름 교정 — twin 없음] {len(renames)}건")
    for r, fix, _ in renames:
        print(f"  #{r['works_id']} {r['works_name'][:24]:24} "
              f"{r['artist_name']!r} → {fix['artist_name']!r}")

    print(f"\n[병합 — clean twin 존재] {len(merges)}건")
    for r, fix, twin in merges:
        keep_desc = twin['description'] or r['description'] or ''
        print(f"  오염 #{r['works_id']} {r['artist_name']!r} (desc {len(r['description'] or '')}자)")
        print(f"    → twin #{twin['works_id']} 유지 [{fix['artist_name']!r}] "
              f"(병합 후 desc {len(keep_desc)}자), 오염행 삭제")

    if not args.apply:
        print(f"\n💡 DRY-RUN 입니다. 실제 반영하려면: python scripts/fix_phantom_artists.py --apply")
        conn.close()
        return

    wcur = conn.cursor()
    # 1) 이름 교정
    for r, fix, _ in renames:
        wcur.execute(
            "UPDATE works SET artist_name=%s, author=%s, illustrator=%s, "
            "original_author=%s WHERE works_id=%s",
            (fix['artist_name'], fix['author'], fix['illustrator'],
             fix['original_author'], r['works_id']),
        )
    # 2) 병합
    for r, fix, twin in merges:
        keep_id, drop_id = twin['works_id'], r['works_id']
        # 서비스 테이블(topic_room, 즐겨찾기 등) 참조를 먼저 keeper 로 이전.
        # 충돌 잔여가 있으면 고아 참조(API NPE)를 만들지 않도록 이 병합은 스킵.
        if not migrate_service_refs(wcur, keep_id, drop_id):
            continue
        # twin의 빈 필드를 오염 행 값으로 채움
        wcur.execute(
            "UPDATE works SET "
            "description=COALESCE(NULLIF(description,''), %s), "
            "thumbnail_url=COALESCE(NULLIF(thumbnail_url,''), %s), "
            "age_classification=COALESCE(NULLIF(age_classification,''), %s), "
            "genre=COALESCE(NULLIF(genre,''), %s) WHERE works_id=%s",
            (r['description'], r['thumbnail_url'], r['age_classification'],
             r['genre'], keep_id),
        )
        # 링크 이전 후 오염 행 삭제
        wcur.execute("INSERT IGNORE INTO works_platform (works_id, platform) "
                     "SELECT %s, platform FROM works_platform WHERE works_id=%s",
                     (keep_id, drop_id))
        wcur.execute("INSERT IGNORE INTO works_hashtag (works_id, hashtag_id) "
                     "SELECT %s, hashtag_id FROM works_hashtag WHERE works_id=%s",
                     (keep_id, drop_id))
        wcur.execute("DELETE FROM works_platform WHERE works_id=%s", (drop_id,))
        wcur.execute("DELETE FROM works_hashtag WHERE works_id=%s", (drop_id,))
        wcur.execute("DELETE FROM works WHERE works_id=%s", (drop_id,))

    conn.commit()
    print(f"\n✅ 적용 완료 — 이름교정 {len(renames)}건, 병합 {len(merges)}건")
    conn.close()


if __name__ == '__main__':
    main()
