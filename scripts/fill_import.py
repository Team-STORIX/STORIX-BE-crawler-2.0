"""
fill_import.py — 채우기 크롤(search_titles) 결과를 '중복 안 만들고' DB에 반영

일반 batch import 는 (works_name + artist_name) 으로 기존 행을 찾으므로, 재크롤한
작가명이 DB 와 조금이라도 다르면 기존 빈 행을 채우지 않고 새 행을 하나 더 만든다.
이 스크립트는 그 문제를 막는다. works_name 으로 기존 행을 찾아 아래로 분기한다:

  · 크롤 작가 == 기존 작가        → 그 행을 정상 채움 (save_one_row)
  · 기존 작가가 비어있음          → 그 행에 작가명 세팅 후 채움 (ADOPT)
  · 기존 작가가 이미 다른 값      → 채우지 않고 검수큐(별도 폴더)에 저장 [CONFLICT]
  · 빈 작가 행이 여러 개 / 매칭 works_name 없음 → 검수큐 [모호 / 제목불일치]

채우는 값의 병합 규칙은 기존 save_one_row 와 동일하다(빈 값 유지·채워진 값 반영,
해시태그는 있을 때만 교체, 플랫폼은 INSERT IGNORE 로 추가).

    python scripts/fill_import.py --input output/2026-07-25/search_titles.jsonl
    python scripts/fill_import.py --input output/2026-07-25/ --dry-run
    python scripts/fill_import.py --input <경로> --review-dir output/fill_review
"""
import sys
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MYSQL_CONFIG, OUTPUT_DIR
from modules.db_handler import connect_database, normalize_data, save_one_row
from batch.fallback import queue_for_review


def _iter_records(input_path: Path):
    """입력이 파일이면 그 파일, 디렉토리면 그 안의 *.jsonl 을 순회하며 레코드를 yield."""
    files = []
    if input_path.is_dir():
        files = sorted(input_path.glob('*.jsonl'))
    elif input_path.is_file():
        files = [input_path]
    else:
        raise SystemExit(f'입력 경로를 찾을 수 없습니다: {input_path}')

    for f in files:
        # 검수큐 산출물은 다시 먹지 않도록 스킵
        if 'review_queue' in f.name or 'conflict' in f.name:
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


def _classify(cursor, works_name: str, artist: str):
    """(action, target_works_id, detail) 반환. action ∈ FILL/ADOPT/CONFLICT/AMBIGUOUS/NO_WORK/NO_ARTIST."""
    if not artist:
        return 'NO_ARTIST', None, '크롤 결과에 작가명 없음'

    cursor.execute(
        "SELECT works_id, COALESCE(artist_name, '') FROM works WHERE works_name = %s "
        "ORDER BY works_id",
        (works_name,),
    )
    rows = cursor.fetchall()
    if not rows:
        return 'NO_WORK', None, 'DB에 동일 works_name 없음 (크롤 제목이 DB와 다를 수 있음)'

    if any(a.strip() == artist for _, a in rows):
        return 'FILL', None, '기존 작가 일치'

    empty = [wid for wid, a in rows if not a.strip()]
    if len(empty) == 1:
        return 'ADOPT', empty[0], '빈 작가 행에 작가명 채움'
    if len(empty) > 1:
        return 'AMBIGUOUS', None, f'빈 작가 행 {len(empty)}개 — 대상 모호'

    db_artists = ', '.join(sorted({a.strip() for _, a in rows}))
    return 'CONFLICT', None, f'기존 작가({db_artists}) ≠ 크롤 작가({artist}) — 중복 방지 보류'


def main():
    ap = argparse.ArgumentParser(description='채우기 크롤 결과를 중복 없이 DB 반영')
    ap.add_argument('--input', required=True, help='search_titles JSONL 파일 또는 디렉토리')
    ap.add_argument('--review-dir', default=str(OUTPUT_DIR / 'fill_review'),
                    help='검수큐 저장 폴더 (기본: output/fill_review)')
    ap.add_argument('--dry-run', action='store_true',
                    help='DB 변경 없이 분류 결과만 집계')
    args = ap.parse_args()

    input_path = Path(args.input)
    review_path = Path(args.review_dir) / 'artist_conflict_queue.jsonl'

    conn = connect_database(MYSQL_CONFIG)
    if not conn:
        raise SystemExit('DB 연결 실패')
    cur = conn.cursor()  # save_one_row 가 정수 인덱싱을 쓰므로 일반 커서 사용

    counts = {k: 0 for k in
              ['FILL', 'ADOPT', 'CONFLICT', 'AMBIGUOUS', 'NO_WORK', 'NO_ARTIST', 'ERROR']}
    reviewed = 0

    for record in _iter_records(input_path):
        norm = normalize_data(record)
        works_name = norm['works_name']
        artist = norm['artist_name']
        if not works_name:
            continue

        action, target, detail = _classify(cur, works_name, artist)

        if args.dry_run:
            counts[action] += 1
            tag = {'FILL': '✅', 'ADOPT': '🩹', 'CONFLICT': '🚧',
                   'AMBIGUOUS': '❓', 'NO_WORK': '🔍', 'NO_ARTIST': '❔'}.get(action, '·')
            print(f'  {tag} [{action:9}] {works_name[:30]:30} | {detail}')
            continue

        try:
            if action == 'FILL':
                save_one_row(conn, cur, record)
                counts['FILL'] += 1
            elif action == 'ADOPT':
                # 빈 작가 행을 이 작가로 '입양' → 이후 save_one_row 가 그 행을 찾아 채운다.
                cur.execute(
                    "UPDATE works SET artist_name = %s "
                    "WHERE works_id = %s AND (artist_name IS NULL OR artist_name = '')",
                    (artist, target),
                )
                save_one_row(conn, cur, record)
                counts['ADOPT'] += 1
            else:
                # CONFLICT / AMBIGUOUS / NO_WORK / NO_ARTIST → 검수큐(별도 폴더)
                queue_for_review(record, f'[{action}] {detail}', review_path)
                counts[action] += 1
                reviewed += 1
        except Exception as e:
            print(f'  ❌ [{works_name}] 처리 실패: {e}')
            counts['ERROR'] += 1

    conn.close()

    print(f"\n{'='*70}")
    print('채우기 임포트 결과' + (' (dry-run)' if args.dry_run else ''))
    print(f"  ✅ FILL      기존 작가 일치, 정상 채움      : {counts['FILL']}")
    print(f"  🩹 ADOPT     빈 작가 행에 작가명 채움       : {counts['ADOPT']}")
    print(f"  🚧 CONFLICT  기존 작가와 불일치 → 검수큐    : {counts['CONFLICT']}")
    print(f"  ❓ AMBIGUOUS 빈 작가 행 다수 → 검수큐       : {counts['AMBIGUOUS']}")
    print(f"  🔍 NO_WORK   works_name 매칭 없음 → 검수큐  : {counts['NO_WORK']}")
    print(f"  ❔ NO_ARTIST 크롤 작가 없음 → 검수큐        : {counts['NO_ARTIST']}")
    if counts['ERROR']:
        print(f"  ❌ ERROR     처리 실패                     : {counts['ERROR']}")
    print(f"{'='*70}")
    if reviewed and not args.dry_run:
        print(f'🚧 검수큐 {reviewed}건 저장: {review_path}')
        print(f'   조회: python cli.py batch review --input {review_path}')


if __name__ == '__main__':
    main()
