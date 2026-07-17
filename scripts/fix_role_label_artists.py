"""
fix_role_label_artists.py — 역할 라벨('글/그림/원작')이 이름에 붙어 오염된 작가 필드 교정

배경:
    구버전 네이버 웹툰 크롤러는 작가 영역 span 텍스트를 통째로 가져와
    artist_name이 '홍끼 ∙ 글/그림', '상금 글 그림'처럼 역할 라벨을 포함했다.
    구버전 parse_artists()는 이름 '앞'의 라벨만 인식해서, 이런 이름-뒤-라벨
    형식은 라벨이 이름에 붙은 채 author/illustrator까지 오염됐다.
    (크롤러·parse_artists 는 수정 완료 — 이 스크립트는 기존 DB 행 정리용)

판별 규칙:
    artist_name 을 '∙'/'글/그림' 정규화 후 공백·콤마 토큰으로 나눴을 때,
    토큰 '전체'가 역할 키워드(글|그림|원작|각색)인 토큰이 하나라도 있으면 오염.
    '글쓰는기계'(이름의 일부), '그림자'(부분 일치) 는 건드리지 않는다.

동작 (scripts/fix_phantom_artists.py 와 동일 패턴):
    - 오염 행의 artist_name 을 콤마 엔트리별로 FIXED parse_artists 에 통과시켜
      author/illustrator/original_author 재산출 → canonical artist_name 계산.
    - 같은 works_name + 교정된 artist_name 행(clean twin)이 이미 있으면 → 병합
      (twin의 빈 필드 채움, works_platform/works_hashtag 이전, 오염 행 삭제).
    - 없으면 → 오염 행의 작가 필드만 교정(UPDATE).
    - 오염 행 여러 개가 같은 교정 결과로 수렴하면 첫 행을 keeper로 삼아 병합.

기본은 DRY-RUN(변경 없음). 실제 적용은 --apply.
    python scripts/fix_role_label_artists.py           # 미리보기
    python scripts/fix_role_label_artists.py --apply    # 실제 반영
"""
import sys
import re
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MYSQL_CONFIG
from modules.db_handler import (
    connect_database,
    parse_artists,
    _canonical_artist_name,
    _ROLE_TOKEN_RE,
)
from scripts.works_ref_migration import migrate_service_refs


def _has_role_label(artist_name: str) -> bool:
    """토큰 전체가 역할 키워드인 토큰이 있는지 (부분 일치 이름은 제외)."""
    text = (
        artist_name.replace('∙', ' ')
        .replace('글/그림', '글 그림')
        .replace('글/원작', '글 원작')
    )
    return any(
        _ROLE_TOKEN_RE.match(tok)
        for tok in re.split(r'[\s,]+', text)
        if tok
    )


def corrected_fields(artist_name: str) -> dict | None:
    """오염된 artist_name → 교정된 작가 필드. 오염이 아니면 None."""
    if not artist_name or not _has_role_label(artist_name):
        return None

    # 콤마 엔트리별로 파싱해 역할 병합 (예: '상금 글 그림,상금 글 그림')
    author = illustrator = original = None
    for entry in (e.strip() for e in artist_name.split(',')):
        if not entry:
            continue
        a, i, o = parse_artists(entry)
        author = author or a
        illustrator = illustrator or i
        original = original or o

    canonical = _canonical_artist_name(artist_name, author, illustrator, original)
    if canonical == artist_name:
        return None
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

    # 후보: 역할 키워드나 '∙'가 들어간 행 (정밀 판별은 파이썬에서)
    cur.execute(
        "SELECT works_id, works_name, artist_name, author, illustrator, "
        "original_author, description, thumbnail_url, age_classification, "
        "genre, works_type FROM works "
        "WHERE artist_name LIKE '%글%' OR artist_name LIKE '%그림%' "
        "OR artist_name LIKE '%원작%' OR artist_name LIKE '%각색%' "
        "OR artist_name LIKE '%∙%'"
    )
    rows = cur.fetchall()

    renames, merges = [], []
    planned: dict[tuple[str, str], dict] = {}  # 교정 결과가 같은 오염 행들 수렴용
    for r in rows:
        fix = corrected_fields(r['artist_name'] or '')
        if not fix:
            continue  # 정상 행 → 스킵

        # clean twin: DB에 이미 있는 행 우선, 없으면 이번 실행에서 같은 결과로 교정 예정인 행
        cur.execute(
            "SELECT works_id, description, thumbnail_url, age_classification, "
            "genre FROM works WHERE works_name=%s AND artist_name=%s AND works_id<>%s",
            (r['works_name'], fix['artist_name'], r['works_id']),
        )
        twin = cur.fetchone()
        if not twin:
            key = (r['works_name'], fix['artist_name'])
            if key in planned:
                twin = planned[key]
            else:
                planned[key] = r
        (merges if twin else renames).append((r, fix, twin))

    print(f"\n{'='*70}")
    print(f"역할 라벨 오염 행: {len(renames)+len(merges)}건  |  이름교정 {len(renames)} · 병합 {len(merges)}")
    print(f"{'='*70}")

    print(f"\n[이름 교정 — twin 없음] {len(renames)}건")
    for r, fix, _ in renames:
        print(f"  #{r['works_id']} {r['works_name'][:24]:24} "
              f"{r['artist_name']!r} → {fix['artist_name']!r}")

    print(f"\n[병합 — clean twin 존재] {len(merges)}건")
    for r, fix, twin in merges:
        print(f"  오염 #{r['works_id']} {r['works_name'][:24]:24} {r['artist_name']!r}")
        print(f"    → twin #{twin['works_id']} 유지 [{fix['artist_name']!r}], 오염행 삭제")

    if not args.apply:
        print(f"\n💡 DRY-RUN 입니다. 실제 반영하려면: python scripts/fix_role_label_artists.py --apply")
        conn.close()
        return

    wcur = conn.cursor()
    # 1) 이름 교정 (병합 keeper가 될 수 있으므로 먼저 실행)
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
        wcur.execute(
            "UPDATE works SET "
            "description=COALESCE(NULLIF(description,''), %s), "
            "thumbnail_url=COALESCE(NULLIF(thumbnail_url,''), %s), "
            "age_classification=COALESCE(NULLIF(age_classification,''), %s), "
            "genre=COALESCE(NULLIF(genre,''), %s) WHERE works_id=%s",
            (r['description'], r['thumbnail_url'], r['age_classification'],
             r['genre'], keep_id),
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

    conn.commit()
    print(f"\n✅ 적용 완료 — 이름교정 {len(renames)}건, 병합 {len(merges)}건")
    conn.close()


if __name__ == '__main__':
    main()
