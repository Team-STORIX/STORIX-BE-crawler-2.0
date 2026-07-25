"""
fix_series_age_badge.py — 이미 적재된 NAVER_SERIES 행의 '19' 배지 오염 제목 정리

원인: 네이버 시리즈 상세 제목 <h2> 안의 연령 배지(<span class="ico_age2">19</span>)가
     .text 에 딸려 들어가 works_name 이 '19테이밍...','191305호'처럼 저장됨.
     (크롤러는 이미 수정됨 — 이 스크립트는 '과거에 적재된 행'을 정리한다.)

'191305호'처럼 배지 뒤가 숫자로 이어지면 문자열만으로는 '19'를 못 떼므로,
JSONL 의 source_url 로 '재크롤' 해서 깨끗한 제목을 확보한 뒤 DB 를 고친다.

처리 (works_name 기준):
  · 깨끗한 이름 행이 없음  → RENAME  : UPDATE works SET works_name = 깨끗한이름
  · 깨끗한 이름 행이 이미 있음 → CONFLICT: 자동 병합/삭제는 안 함(운영 FK 위험).
                               두 works_id 와 수동 병합용 SQL 을 리포트로 남김.
  · bad 이름 행 여러 개      → AMBIGUOUS: 리포트

기본은 dry-run(재크롤만, DB 미변경). 실제 반영은 --apply.

    python scripts/fix_series_age_badge.py --input output/2026-07-25/            # 미리보기
    python scripts/fix_series_age_badge.py --input output/2026-07-25/ --apply    # RENAME 반영
"""
import sys
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MYSQL_CONFIG, OUTPUT_DIR
from modules.db_handler import connect_database
from modules.crawler.naver_series_crawler import NaverSeriesCrawler

# 연령 배지로 오염될 수 있는 접두(네이버 시리즈: 19/15/12). 재크롤 대상 좁히기용 —
# 최종 판정은 '재크롤한 깨끗한 이름 != 저장된 이름' 비교로 하므로 정상 제목은 걸러진다.
_BADGE_PREFIXES = ('19', '15', '12')


def _iter_records(input_path: Path):
    files = sorted(input_path.glob('*.jsonl')) if input_path.is_dir() else [input_path]
    for f in files:
        if not f.exists() or 'review_queue' in f.name or 'conflict' in f.name:
            continue
        with open(f, encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def _collect_candidates(input_path: Path) -> list[tuple[str, str]]:
    """(저장된 오염 이름, source_url) 후보를 중복 없이 수집."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for r in _iter_records(input_path):
        if r.get('platform') != 'NAVER_SERIES':
            continue
        name = (r.get('works_name') or '').strip()
        url = (r.get('source_url') or '').strip()
        if not name or not url or 'series.naver.com' not in url:
            continue
        if not name.startswith(_BADGE_PREFIXES):
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append((name, url))
    return out


def main():
    ap = argparse.ArgumentParser(description="NAVER_SERIES '19' 배지 오염 제목 정리")
    ap.add_argument('--input', required=True, help='적재에 쓴 JSONL 파일/디렉토리')
    ap.add_argument('--apply', action='store_true', help='실제 RENAME 반영 (기본: dry-run)')
    ap.add_argument('--report', default=str(OUTPUT_DIR / 'series_badge_conflicts.jsonl'),
                    help='CONFLICT/AMBIGUOUS 리포트 경로')
    args = ap.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f'입력 경로 없음: {input_path}')

    candidates = _collect_candidates(input_path)
    print(f"재크롤 후보(NAVER_SERIES, '19/15/12'로 시작): {len(candidates)}건")
    if not candidates:
        return

    conn = connect_database(MYSQL_CONFIG)
    if not conn:
        raise SystemExit('DB 연결 실패')
    cur = conn.cursor()

    report_path = Path(args.report)
    counts = {'RENAME': 0, 'CONFLICT': 0, 'AMBIGUOUS': 0, 'NOT_IN_DB': 0, 'CLEAN': 0, 'RECRAWL_FAIL': 0}
    conflicts: list[dict] = []

    crawler = NaverSeriesCrawler()
    crawler.start_driver()
    try:
        if not crawler.login():
            raise SystemExit('네이버 로그인 실패')

        for i, (bad_name, url) in enumerate(candidates, 1):
            result = crawler.crawl_detail_with_retry(url)
            clean_name = (result or {}).get('works_name', '').strip() if result else ''
            if not clean_name:
                print(f"  [{i}/{len(candidates)}] ❌ 재크롤 실패: {url}")
                counts['RECRAWL_FAIL'] += 1
                continue
            if clean_name == bad_name:
                counts['CLEAN'] += 1  # 배지 아님(정상 제목이 우연히 19로 시작)
                continue

            # bad 이름 행 조회
            cur.execute("SELECT works_id FROM works WHERE works_name = %s", (bad_name,))
            bad_rows = [r[0] for r in cur.fetchall()]
            if not bad_rows:
                counts['NOT_IN_DB'] += 1
                continue
            if len(bad_rows) > 1:
                counts['AMBIGUOUS'] += 1
                conflicts.append({'type': 'AMBIGUOUS', 'bad': bad_name, 'clean': clean_name,
                                  'bad_works_ids': bad_rows, 'url': url})
                print(f"  [{i}/{len(candidates)}] ❓ AMBIGUOUS '{bad_name}' → 행 {len(bad_rows)}개")
                continue

            # 깨끗한 이름 행이 이미 있는지
            cur.execute("SELECT works_id FROM works WHERE works_name = %s", (clean_name,))
            clean_rows = [r[0] for r in cur.fetchall()]
            if clean_rows:
                counts['CONFLICT'] += 1
                merge_sql = (
                    f"-- '{bad_name}'(id={bad_rows[0]}) → '{clean_name}'(id={clean_rows[0]}) 수동 병합\n"
                    f"INSERT IGNORE INTO works_platform (works_id, platform) "
                    f"SELECT {clean_rows[0]}, platform FROM works_platform WHERE works_id={bad_rows[0]};\n"
                    f"INSERT IGNORE INTO works_hashtag (works_id, hashtag_id) "
                    f"SELECT {clean_rows[0]}, hashtag_id FROM works_hashtag WHERE works_id={bad_rows[0]};\n"
                    f"-- (앱 참조 확인 후) DELETE FROM works WHERE works_id={bad_rows[0]};"
                )
                conflicts.append({'type': 'CONFLICT', 'bad': bad_name, 'clean': clean_name,
                                  'bad_works_id': bad_rows[0], 'clean_works_id': clean_rows[0],
                                  'url': url, 'merge_sql': merge_sql})
                print(f"  [{i}/{len(candidates)}] 🚧 CONFLICT '{bad_name}'(id={bad_rows[0]}) "
                      f"↔ 기존 '{clean_name}'(id={clean_rows[0]}) — 리포트")
                continue

            # 단순 RENAME
            if args.apply:
                cur.execute("UPDATE works SET works_name = %s WHERE works_id = %s",
                            (clean_name, bad_rows[0]))
                conn.commit()
            counts['RENAME'] += 1
            print(f"  [{i}/{len(candidates)}] {'✏️  RENAME' if args.apply else '👀 RENAME(dry)'} "
                  f"'{bad_name}' → '{clean_name}' (id={bad_rows[0]})")
    finally:
        crawler.close_driver()
        conn.close()

    if conflicts:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, 'w', encoding='utf-8') as f:
            for c in conflicts:
                f.write(json.dumps(c, ensure_ascii=False) + '\n')

    print(f"\n{'='*70}")
    print('배지 제목 정리 결과' + ('' if args.apply else ' (dry-run — DB 미변경)'))
    print(f"  ✏️  RENAME     깨끗한 이름으로 변경        : {counts['RENAME']}")
    print(f"  🚧 CONFLICT   기존 깨끗한 행과 충돌(수동)  : {counts['CONFLICT']}")
    print(f"  ❓ AMBIGUOUS  동일 오염 이름 행 다수       : {counts['AMBIGUOUS']}")
    print(f"  🔍 NOT_IN_DB  DB에 없음(이미 정리됨 등)    : {counts['NOT_IN_DB']}")
    print(f"  ➖ CLEAN      배지 아님(정상 제목)         : {counts['CLEAN']}")
    print(f"  ❌ RECRAWL    재크롤 실패                  : {counts['RECRAWL_FAIL']}")
    print(f"{'='*70}")
    if conflicts:
        print(f"🚧 CONFLICT/AMBIGUOUS {len(conflicts)}건 리포트: {report_path}")
        print("   각 CONFLICT 항목의 merge_sql 로 수동 병합하세요 (앱 FK 확인 후 DELETE).")
    if not args.apply and counts['RENAME']:
        print("\n실제 반영하려면 --apply 를 붙여 다시 실행하세요.")


if __name__ == '__main__':
    main()
