"""
review_similar_works.py — works_name 이 80% 이상 유사한 작품쌍을 사람이 검수·병합

크롤 소스/표기 차이로 '나 혼자만 레벨업' vs '나혼자만 레벨업' 처럼 사실상 같은
작품이 두 행으로 갈린 경우를 잡는다. 이름 유사도가 임계값(기본 0.8) 이상인 쌍을
모아, 한 쌍씩 화면에 나란히 띄우고 사람에게 물어본다:

    1 = [1]번 작품을 기준(우선)으로 [2]를 흡수 병합
    2 = [2]번 작품을 기준(우선)으로 [1]을 흡수 병합
    s = 건너뛰기        q = 종료

'기준'으로 고른 행의 값이 우선이며, 비어 있는 필드만 상대 행의 값으로 채운다.
그 뒤 상대 행의 플랫폼·해시태그·서비스 참조(즐겨찾기/토픽룸 등)를 기준 행으로
옮기고 상대 행을 삭제한다. 참조 이전은 merge_split_works 와 동일하게
works_ref_migration.migrate_service_refs 를 쓴다 — 유니크 충돌로 못 옮기는
사용자 데이터가 남으면 그 행은 삭제하지 않고 경고만 남긴다(고아 참조 방지).

⚠️ 이 스크립트는 각 선택을 '즉시 DB 에 반영'한다. 먼저 --dry-run 으로 후보만
   확인한 뒤 실행하는 것을 권장한다.

    python scripts/review_similar_works.py --dry-run          # 후보쌍만 나열
    python scripts/review_similar_works.py                    # 검수 시작 (기본 임계값 0.8)
    python scripts/review_similar_works.py --threshold 0.85   # 더 엄격하게
    python scripts/review_similar_works.py --type 웹툰         # 웹툰끼리만 비교
"""
import re
import sys
import argparse
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MYSQL_CONFIG
from modules.db_handler import connect_database
from scripts.works_ref_migration import migrate_service_refs

# 병합 시 '기준 행이 비어 있으면' 상대 행 값으로 채우는 필드들.
FILLABLE_FIELDS = [
    'artist_name', 'author', 'illustrator', 'original_author',
    'genre', 'age_classification', 'thumbnail_url', 'description',
]

LOAD_QUERY = """
    SELECT w.works_id,
           w.works_name,
           COALESCE(w.artist_name, '')       AS artist_name,
           COALESCE(w.author, '')            AS author,
           COALESCE(w.illustrator, '')       AS illustrator,
           COALESCE(w.original_author, '')   AS original_author,
           COALESCE(w.works_type, '')        AS works_type,
           COALESCE(w.genre, '')             AS genre,
           COALESCE(w.age_classification,'') AS age_classification,
           COALESCE(w.thumbnail_url, '')     AS thumbnail_url,
           COALESCE(w.description, '')       AS description,
           (SELECT GROUP_CONCAT(DISTINCT platform ORDER BY platform SEPARATOR ', ')
              FROM works_platform WHERE works_id = w.works_id) AS platforms,
           (SELECT COUNT(*) FROM works_hashtag WHERE works_id = w.works_id) AS hashtag_count
    FROM works w
    ORDER BY w.works_id
"""


def normalize_name(name: str) -> str:
    """공백·기호를 제거하고 소문자화한 비교용 이름. (한글/영문/숫자만 남김)"""
    if not name:
        return ''
    # \W 는 유니코드 단어문자(한글 포함)를 제외한 모든 것 → 공백·기호·구두점 제거
    return re.sub(r'[\s\W_]+', '', name.lower())


# '같은 작품의 다른 편'을 가리키는 시퀄/시즌 표기들. 이것만 다르면 별개 작품으로 본다.
# 주의: '개정판/단행본/완전판' 같은 '판본' 표기는 여기 넣지 않는다(같은 작품 → 병합 대상).
_SEQUEL_PATTERNS = [
    r'시즌\s*\d+',          # 시즌2, 시즌 3
    r'\d+\s*부',            # 1부, 6부
    r'\d+\s*학기',          # 2학기
    r'외전', r'번외',        # 외전 / 번외
    r'(?:^|\s)(?:19|20)\d{2}(?=\s|$)',  # 앞머리 연도 (2025 루키 단편선)
]


def _split_sequel(name: str):
    """(base, marker) 반환 — 시퀄 표기를 떼어낸 정규화 기준명과, 떼어낸 표기 집합."""
    s = name.lower()
    markers = []
    for pat in _SEQUEL_PATTERNS:
        for m in re.findall(pat, s):
            markers.append(re.sub(r'\s+', '', m))
        s = re.sub(pat, ' ', s)
    # 남은 맨 뒤 숫자 (마음의소리2, 창천무신2, 야쿠자가 사랑을 한다면2)
    tail = re.search(r'(\d+)\s*$', s)
    if tail:
        markers.append(tail.group(1))
        s = s[:tail.start()]
    return normalize_name(s), tuple(sorted(markers))


def is_sequel_pair(a: str, b: str) -> bool:
    """기준명은 같은데 시퀄/시즌/연도 표기만 다르면 True (= 별개 편, 병합 후보 제외)."""
    base_a, mark_a = _split_sequel(a)
    base_b, mark_b = _split_sequel(b)
    return bool(base_a) and base_a == base_b and mark_a != mark_b


def name_similarity(a: str, b: str, threshold: float) -> float:
    """정규화 이름 a,b 의 유사도(0~1). 임계값 미만이 확정되면 조기에 0.0 반환."""
    if not a or not b:
        return 0.0
    la, lb = len(a), len(b)
    # 길이 상한 프리필터: ratio <= 2*min/(min+max). 상한이 임계값보다 작으면 계산 생략.
    if 2 * min(la, lb) / (la + lb) < threshold:
        return 0.0
    sm = SequenceMatcher(None, a, b)
    if sm.real_quick_ratio() < threshold or sm.quick_ratio() < threshold:
        return 0.0
    return sm.ratio()


def find_candidate_pairs(rows, threshold: float, same_type_only: bool, skip_sequels: bool):
    """유사도 >= threshold 인 (score, row_a, row_b) 목록을 점수 내림차순으로 반환.

    반환: (pairs, sequel_skipped_count)
    """
    enriched = [(r, normalize_name(r['works_name'])) for r in rows]
    pairs = []
    sequel_skipped = 0
    n = len(enriched)
    for i in range(n):
        ri, ni = enriched[i]
        if not ni:
            continue
        for j in range(i + 1, n):
            rj, nj = enriched[j]
            if not nj:
                continue
            if same_type_only and ri['works_type'] != rj['works_type']:
                continue
            score = name_similarity(ni, nj, threshold)
            if score < threshold:
                continue
            if skip_sequels and is_sequel_pair(ri['works_name'], rj['works_name']):
                sequel_skipped += 1
                continue
            pairs.append((score, ri, rj))
    pairs.sort(key=lambda p: p[0], reverse=True)
    return pairs, sequel_skipped


def _fmt_work(tag: str, r: dict) -> str:
    desc_len = len(r['description'])
    thumb = '있음' if r['thumbnail_url'] else '없음'
    platforms = r['platforms'] or '없음'
    names = ' / '.join(x for x in [
        f"글:{r['author']}" if r['author'] else '',
        f"그림:{r['illustrator']}" if r['illustrator'] else '',
        f"원작:{r['original_author']}" if r['original_author'] else '',
    ] if x) or (r['artist_name'] or '(작가 없음)')
    return (
        f" [{tag}] #{r['works_id']}  [{r['works_type'] or '?'}]  {r['works_name']}\n"
        f"       작가 : {names}\n"
        f"       장르 : {r['genre'] or '-':6} | 연령 : {r['age_classification'] or '-':8} | 썸네일 : {thumb}\n"
        f"       설명 : {desc_len}자 | 플랫폼 : {platforms} | 해시태그 : {r['hashtag_count']}개"
    )


def merge_pair(conn, wcur, keeper: dict, loser: dict) -> bool:
    """keeper 를 우선으로 loser 를 흡수. 반영 성공 시 True.

    keeper 의 '빈' 필드만 loser 값으로 채우고, loser 의 플랫폼/해시태그/서비스
    참조를 keeper 로 옮긴 뒤 loser 행을 삭제한다. 서비스 참조 유니크 충돌로
    loser 를 지울 수 없으면 채우기까지만 하고 삭제는 건너뛴다(고아 참조 방지).
    """
    kid, lid = keeper['works_id'], loser['works_id']

    # 1) keeper 의 빈 필드를 loser 값으로 채움 (keeper 우선)
    set_clauses = ", ".join(f"{f} = COALESCE(NULLIF({f}, ''), %s)" for f in FILLABLE_FIELDS)
    params = [loser[f] for f in FILLABLE_FIELDS]
    # works_type 도 비어 있으면 채움
    wcur.execute(
        f"UPDATE works SET {set_clauses}, "
        f"works_type = COALESCE(NULLIF(works_type, ''), %s) WHERE works_id = %s",
        (*params, loser['works_type'], kid),
    )

    # 2) 서비스 테이블 참조 이전 (즐겨찾기/토픽룸 등)
    if not migrate_service_refs(wcur, kid, lid):
        # 충돌 잔여 → loser 삭제 불가. 채우기만 커밋하고 병합 미완으로 보고.
        conn.commit()
        print(f"  ⚠️ #{lid} 삭제 보류(사용자 데이터 충돌). #{kid} 필드 채우기만 반영됨.")
        return False

    # 3) 플랫폼/해시태그 이전 후 loser 삭제
    wcur.execute("INSERT IGNORE INTO works_platform (works_id, platform) "
                 "SELECT %s, platform FROM works_platform WHERE works_id = %s", (kid, lid))
    wcur.execute("INSERT IGNORE INTO works_hashtag (works_id, hashtag_id) "
                 "SELECT %s, hashtag_id FROM works_hashtag WHERE works_id = %s", (kid, lid))
    wcur.execute("DELETE FROM works_platform WHERE works_id = %s", (lid,))
    wcur.execute("DELETE FROM works_hashtag WHERE works_id = %s", (lid,))
    wcur.execute("DELETE FROM works WHERE works_id = %s", (lid,))
    conn.commit()
    print(f"  ✅ 병합 완료 — KEEP #{kid} ← 흡수/삭제 #{lid}")
    return True


def main():
    ap = argparse.ArgumentParser(description='이름이 유사한 작품쌍을 사람이 검수·병합')
    ap.add_argument('--threshold', type=float, default=0.8,
                    help='유사도 임계값 0~1 (기본 0.8 = 80%%)')
    ap.add_argument('--type', dest='works_type', default=None,
                    help='특정 works_type 만 대상(예: 웹툰, 웹소설). 미지정 시 전체')
    ap.add_argument('--cross-type', action='store_true',
                    help='다른 works_type 끼리도 비교(기본은 같은 타입끼리만)')
    ap.add_argument('--skip-sequels', action='store_true',
                    help='시즌·N부·외전·연도만 다른 쌍은 후보에서 제외(별개 편). '
                         '개정판·단행본 등 판본 차이는 그대로 후보로 남김')
    ap.add_argument('--dry-run', action='store_true',
                    help='후보쌍만 나열하고 종료(DB 변경 없음)')
    args = ap.parse_args()

    if not 0 < args.threshold <= 1:
        raise SystemExit('--threshold 는 0 초과 1 이하여야 합니다.')

    conn = connect_database(MYSQL_CONFIG)
    if not conn:
        raise SystemExit('DB 연결 실패')

    rcur = conn.cursor(dictionary=True)
    rcur.execute(LOAD_QUERY)
    rows = rcur.fetchall()
    if args.works_type:
        rows = [r for r in rows if r['works_type'] == args.works_type]

    pairs, sequel_skipped = find_candidate_pairs(
        rows, args.threshold,
        same_type_only=not args.cross_type,
        skip_sequels=args.skip_sequels,
    )

    print(f"\n{'=' * 70}")
    print(f"이름 유사도 ≥ {args.threshold:.0%} 후보쌍: {len(pairs)}건"
          + (f"  (타입: {args.works_type})" if args.works_type else "")
          + ("  [교차 타입 비교]" if args.cross_type else ""))
    if args.skip_sequels:
        print(f"  (시퀄/시즌/연도만 다른 {sequel_skipped}쌍은 제외됨)")
    print(f"{'=' * 70}")

    if args.dry_run:
        for score, a, b in pairs:
            print(f"  {score:.0%}  #{a['works_id']} {a['works_name']!r}  ⇄  "
                  f"#{b['works_id']} {b['works_name']!r}")
        print(f"\n💡 DRY-RUN. 검수(병합)하려면 --dry-run 없이 실행하세요.")
        conn.close()
        return

    if not pairs:
        conn.close()
        return

    print("⚠️  각 선택은 즉시 DB 에 반영됩니다.\n")

    wcur = conn.cursor()
    deleted: set[int] = set()
    merged = skipped = 0

    for idx, (score, a, b) in enumerate(pairs, 1):
        # 앞선 병합으로 이미 삭제된 행이 포함된 쌍은 건너뜀
        if a['works_id'] in deleted or b['works_id'] in deleted:
            continue

        print(f"\n{'=' * 70}")
        print(f"유사 작품 후보  {idx}/{len(pairs)}   (유사도 {score:.0%})")
        print(f"{'=' * 70}")
        print(_fmt_work('1', a))
        print(_fmt_work('2', b))
        print('-' * 70)
        print(" 1 = [1]번 기준으로 병합(2 흡수)    2 = [2]번 기준으로 병합(1 흡수)")
        print(" s = 건너뛰기                        q = 종료")

        try:
            choice = input("선택> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n종료합니다.")
            break

        if choice == 'q':
            break
        if choice in ('', 's'):
            skipped += 1
            continue
        if choice == '1':
            keeper, loser = a, b
        elif choice == '2':
            keeper, loser = b, a
        else:
            print("  ↩︎ 알 수 없는 입력 — 건너뜁니다.")
            skipped += 1
            continue

        try:
            if merge_pair(conn, wcur, keeper, loser):
                deleted.add(loser['works_id'])
                merged += 1
            else:
                skipped += 1
        except Exception as e:
            conn.rollback()
            print(f"  ❌ 병합 실패: {e}")
            skipped += 1

    conn.close()
    print(f"\n{'=' * 70}")
    print(f"검수 종료 — 병합 {merged}건 / 건너뜀 {skipped}건")
    print(f"{'=' * 70}")


if __name__ == '__main__':
    main()
