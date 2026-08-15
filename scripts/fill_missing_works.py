"""
fill_missing_works.py — 해시태그 또는 플랫폼이 비어있는 works를 찾아 titles.txt로 출력

works_hashtag / works_platform 조인 결과 둘 중 하나 이상이 0인 works를 뽑아,
works_type(웹툰 | 웹소설)별 섹션 헤더 형식으로 titles.txt에 기록한다.
이 파일을 그대로 search_titles 모드에 넣어 재크롤 → batch import 하면 빈 값이 채워진다.

기본은 읽기 전용(--dry-run)이며, 실제 기록은 --write 를 줘야 한다.

    python scripts/fill_missing_works.py                 # 대상만 출력 (파일 미변경)
    python scripts/fill_missing_works.py --write         # titles.txt 에 기록
    python scripts/fill_missing_works.py --write -o my_titles.txt

이어지는 채우기 파이프라인:
    python cli.py crawl --platform all --mode search_titles --titles-file titles.txt
    python cli.py batch import
"""
import sys
import argparse
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MYSQL_CONFIG
from modules.db_handler import connect_database

# 사용자 쿼리와 동일한 판정 로직: 해시태그 수 또는 플랫폼 수가 0인 works
MISSING_QUERY = """
    SELECT w.works_id,
           w.works_name,
           COALESCE(w.works_type, '')  AS works_type,
           COUNT(DISTINCT h.id)        AS hashtag_count,
           COUNT(DISTINCT wp.id)       AS platform_count
    FROM works w
             LEFT JOIN works_hashtag wh ON wh.works_id = w.works_id
             LEFT JOIN hashtag h         ON h.id = wh.hashtag_id
             LEFT JOIN works_platform wp ON wp.works_id = w.works_id
    GROUP BY w.works_id, w.works_name, w.works_type
    HAVING COUNT(DISTINCT h.id) = 0
        OR COUNT(DISTINCT wp.id) = 0
    ORDER BY w.works_id
"""

# search_titles 가 인식하는 타입 헤더 (cli._TYPE_HEADERS 와 일치)
# '단행본'은 DB works_type 에 없는 값이라 보통 0건이지만, 손으로 제목을 넣을 자리로
# 빈 섹션을 항상 남겨둔다. (단행본 섹션은 시리즈·카카오·리디에서만 검색됨)
SECTION_TYPES = ['웹툰', '웹소설', '단행본']


def main():
    ap = argparse.ArgumentParser(description='해시태그/플랫폼 빈 works를 titles.txt로 출력')
    ap.add_argument('--write', action='store_true',
                    help='titles.txt 에 실제로 기록 (미지정 시 대상만 출력)')
    ap.add_argument('-o', '--output', default='titles.txt',
                    help='출력 파일 경로 (기본: titles.txt)')
    args = ap.parse_args()

    conn = connect_database(MYSQL_CONFIG)
    if not conn:
        raise SystemExit('DB 연결 실패')

    cur = conn.cursor(dictionary=True)
    cur.execute(MISSING_QUERY)
    rows = cur.fetchall()
    conn.close()

    # works_type 별로 제목 그룹핑 (중복 제거, DB 정렬 순서 유지)
    by_type: dict[str, list[str]] = defaultdict(list)
    seen_by_type: dict[str, set[str]] = defaultdict(set)
    untyped: list[dict] = []

    for r in rows:
        wtype = (r['works_type'] or '').strip()
        name = (r['works_name'] or '').strip()
        if not name:
            continue
        if wtype not in SECTION_TYPES:
            untyped.append(r)  # search_titles 는 타입 없는 제목을 스킵하므로 별도 보고
            continue
        if name not in seen_by_type[wtype]:
            seen_by_type[wtype].add(name)
            by_type[wtype].append(name)

    # 요약 출력
    no_hashtag = sum(1 for r in rows if r['hashtag_count'] == 0)
    no_platform = sum(1 for r in rows if r['platform_count'] == 0)
    print(f"\n{'='*70}")
    print(f"해시태그/플랫폼 빈 works: {len(rows)}건")
    print(f"  ├─ 해시태그 0 : {no_hashtag}건")
    print(f"  └─ 플랫폼   0 : {no_platform}건  (둘 다 0인 경우 양쪽에 중복 집계)")
    for wtype in SECTION_TYPES:
        print(f"  · [{wtype}] {len(by_type[wtype])}건")
    if untyped:
        print(f"  ⚠️ works_type 미지정({'/'.join(SECTION_TYPES)} 아님) {len(untyped)}건 → titles.txt 스킵")
        for r in untyped[:15]:
            print(f"      #{r['works_id']} {r['works_name']!r} type={r['works_type']!r}")
        if len(untyped) > 15:
            print(f"      ... 외 {len(untyped) - 15}건")
    print(f"{'='*70}")

    # 파일 본문 생성
    lines = [
        '# titles.txt — search_titles 모드 작품명 목록',
        '# fill_missing_works.py 자동 생성 (해시태그/플랫폼 빈 works)',
        '# 검색 성공한 작품은 search_titles 실행 후 이 파일에서 자동 제거됩니다.',
        '',
    ]
    for wtype in SECTION_TYPES:
        lines.append(f'## {wtype}')
        lines.extend(by_type[wtype])
        lines.append('')

    body = '\n'.join(lines)

    if not args.write:
        print('\n[미리보기] --write 를 주면 아래 내용을 파일에 기록합니다.\n')
        print(body)
        return

    out = Path(args.output)
    if not out.is_absolute():
        out = Path(__file__).resolve().parent.parent / out
    out.write_text(body, encoding='utf-8')
    print(f"\n✅ {out} 에 {sum(len(v) for v in by_type.values())}건 기록 완료.")
    print("   이어서:")
    print(f"     python cli.py crawl --platform all --mode search_titles --titles-file {args.output}")
    print("     python cli.py batch import")


if __name__ == '__main__':
    main()
