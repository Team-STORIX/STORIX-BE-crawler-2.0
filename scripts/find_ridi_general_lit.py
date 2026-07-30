"""
find_ridi_general_lit.py — 이미 적재된 RIDIBOOKS works 중 '일반 단행본(비웹툰·비웹소설)' 후보를 조회

works 테이블엔 source_url 이 없어 리디 책 URL 을 복원할 수 없으므로, works_name 으로
리디를 재검색(HTTP)해 상위 결과의 책 페이지 브레드크럼 카테고리를 확인한다.
카테고리가 나라별 문학("일본 소설" 등)·비소설 섹션(에세이/인문 등)이면
'일반문학 후보'로 표시한다. (판정 로직은 크롤러의 _ridi_is_general_lit 재사용)

브라우저·로그인 불필요(순수 HTTP). 읽기 전용 — DB 를 바꾸지 않고 후보만 보고한다.
성인(19금) 작품은 비로그인 검색에 안 잡혀 '확인불가'로 분류될 수 있다.

    python scripts/find_ridi_general_lit.py                 # 전체 조회
    python scripts/find_ridi_general_lit.py --limit 20      # 앞 20건만
    python scripts/find_ridi_general_lit.py --delay 1.0     # 요청 간 1초 (기본 0.5)
    python scripts/find_ridi_general_lit.py --out ridi_lit_ids.txt   # 후보 works_id 파일로

이후 정리(수동):
    -- 후보 확인 후 삭제 (서비스 참조 이전이 필요하면 works_ref_migration 참고)
    DELETE FROM works_platform WHERE works_id = <id>;
    DELETE FROM works_hashtag  WHERE works_id = <id>;
    DELETE FROM works          WHERE works_id = <id>;
"""
import re
import sys
import time
import argparse
import urllib.parse
import urllib.request
import urllib.error
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MYSQL_CONFIG
from modules.db_handler import connect_database
from modules.crawler.ridibooks_crawler import _ridi_is_general_lit

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept-Language': 'ko-KR',
}

RIDI_WORKS_QUERY = """
    SELECT w.works_id,
           w.works_name,
           COALESCE(w.artist_name, '') AS artist_name,
           COALESCE(w.genre, '')       AS genre,
           COALESCE(w.works_type, '')  AS works_type,
           (SELECT GROUP_CONCAT(platform ORDER BY platform SEPARATOR '+')
              FROM works_platform WHERE works_id = w.works_id) AS platforms
    FROM works w
    WHERE EXISTS (SELECT 1 FROM works_platform wp
                  WHERE wp.works_id = w.works_id AND wp.platform = 'RIDIBOOKS')
    ORDER BY w.works_id
"""


def _norm(s: str) -> str:
    """제목 비교용 정규화: 공백·괄호·구분기호 제거 후 소문자화."""
    return re.sub(r'[\s\[\]()·∙#\-~!]', '', (s or '')).lower()


def _http_get(url: str, retries: int = 5) -> str:
    """GET. 429(Too Many Requests)면 Retry-After/지수 백오프로 재시도."""
    backoff = 5
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries:
                wait = int(e.headers.get('Retry-After') or 0) or backoff
                print(f"      ⏳ 429 — {wait}s 대기 후 재시도 ({attempt+1}/{retries})")
                time.sleep(wait)
                backoff = min(backoff * 2, 60)
                continue
            raise


def _top_book_id(title: str) -> str | None:
    """리디 검색 상위 결과(_rdt_idx=0)의 책 ID."""
    html = _http_get('https://ridibooks.com/search?q=' + urllib.parse.quote(title))
    m = re.search(r'/books/(\d+)[^"]*_rdt_idx=0', html)
    if m:
        return m.group(1)
    # _rdt_idx 형식이 바뀐 경우 첫 /books/ 링크로 폴백
    m = re.search(r'/books/(\d+)', html)
    return m.group(1) if m else None


def _book_categories(book_id: str) -> tuple[str, list[str]]:
    """책 페이지의 (og:title, 브레드크럼 카테고리 목록). 카테고리는 h1 앞부분만."""
    html = _http_get(f'https://ridibooks.com/books/{book_id}')
    h1 = html.find('<h1')
    head = html[:h1] if h1 > 0 else html
    cats = [
        re.sub(r'<[^>]+>', '', c).strip()
        for c in re.findall(r'<a[^>]+href="/category/\d+[^"]*"[^>]*>(.*?)</a>', head, re.S)
    ]
    # 중복 제거(순서 유지)
    cats = list(dict.fromkeys([c for c in cats if c]))
    og = re.search(r'property="og:title" content="([^"]+)"', html)
    return (og.group(1) if og else ''), cats


def main():
    ap = argparse.ArgumentParser(description='적재된 RIDIBOOKS works 중 일반문학 후보 조회')
    ap.add_argument('--limit', type=int, default=0, help='앞 N건만 검사 (0=전체)')
    ap.add_argument('--delay', type=float, default=0.5, help='요청 간 대기초 (기본 0.5)')
    ap.add_argument('--ids', default=None,
                    help='특정 works_id 만 검사 (쉼표 구분). 429로 빠진 건 재검사용')
    ap.add_argument('--out', default=None, help='일반문학 후보 works_id 를 저장할 파일')
    args = ap.parse_args()

    id_filter = None
    if args.ids:
        id_filter = {int(x) for x in args.ids.replace(' ', '').split(',') if x}

    conn = connect_database(MYSQL_CONFIG)
    if not conn:
        raise SystemExit('DB 연결 실패')
    cur = conn.cursor(dictionary=True)
    cur.execute(RIDI_WORKS_QUERY)
    rows = cur.fetchall()
    conn.close()

    if id_filter is not None:
        rows = [r for r in rows if r['works_id'] in id_filter]
    if args.limit:
        rows = rows[:args.limit]

    print(f"\nRIDIBOOKS 연결 works {len(rows)}건 검사 시작 (delay={args.delay}s)\n")

    flagged: list[dict] = []      # 일반문학 후보
    unknown: list[dict] = []      # 검색실패/제목불일치 → 수동확인
    ok = 0

    for i, r in enumerate(rows, 1):
        name = r['works_name']
        try:
            bid = _top_book_id(name)
            if not bid:
                r['_why'] = '검색 결과 없음(성인작 등)'
                unknown.append(r)
                print(f"  [{i}/{len(rows)}] ❔ {name[:26]} — 검색 결과 없음")
                time.sleep(args.delay)
                continue

            og_title, cats = _book_categories(bid)
            # 상위 결과가 이 작품이 맞는지 제목으로 검증 (오매칭 방지)
            ratio = SequenceMatcher(None, _norm(name), _norm(og_title)).ratio()
            if _norm(name) not in _norm(og_title) and _norm(og_title) not in _norm(name) and ratio < 0.85:
                r['_why'] = f'검색 상위결과 제목 불일치: "{og_title}" (books/{bid})'
                unknown.append(r)
                print(f"  [{i}/{len(rows)}] ❓ {name[:26]} — 상위결과 불일치({og_title[:16]})")
                time.sleep(args.delay)
                continue

            if _ridi_is_general_lit(cats):
                r['_book_id'] = bid
                r['_cats'] = cats
                flagged.append(r)
                print(f"  [{i}/{len(rows)}] 🚫 {name[:26]} — 일반문학 ({' > '.join(cats)})")
            else:
                ok += 1
                print(f"  [{i}/{len(rows)}] ✅ {name[:26]} — 대상 ({' > '.join(cats[:2])})")
        except Exception as e:
            r['_why'] = f'조회 오류: {e}'
            unknown.append(r)
            print(f"  [{i}/{len(rows)}] ⚠️ {name[:26]} — 오류: {e}")
        time.sleep(args.delay)

    print(f"\n{'='*70}")
    print(f"검사 {len(rows)}건 — 🚫 일반문학 후보 {len(flagged)} | ✅ 대상 {ok} | ❔ 수동확인 {len(unknown)}")
    print(f"{'='*70}")

    if flagged:
        print("\n🚫 일반문학 후보 (삭제 검토):")
        for r in flagged:
            print(f"  #{r['works_id']:<6} {r['works_name'][:30]:32} [{r['works_type'] or '?'}] "
                  f"{r['platforms']}  | {' > '.join(r['_cats'])}")
        ids = ','.join(str(r['works_id']) for r in flagged)
        print(f"\n  works_id 목록: {ids}")
        print("  삭제 예시:")
        print(f"    DELETE FROM works_platform WHERE works_id IN ({ids});")
        print(f"    DELETE FROM works_hashtag  WHERE works_id IN ({ids});")
        print(f"    DELETE FROM works          WHERE works_id IN ({ids});")
        print("  ※ 즐겨찾기/토픽룸 등 서비스 참조가 있으면 works_ref_migration 로 먼저 정리 필요")

    if unknown:
        print(f"\n❔ 수동확인 {len(unknown)}건 (검색 실패·제목 불일치 — 성인작 포함 가능):")
        for r in unknown[:30]:
            print(f"  #{r['works_id']:<6} {r['works_name'][:30]:32} — {r['_why']}")
        if len(unknown) > 30:
            print(f"  ... 외 {len(unknown) - 30}건")

    if args.out and flagged:
        out = Path(args.out)
        if not out.is_absolute():
            out = Path(__file__).resolve().parent.parent / out
        out.write_text('\n'.join(str(r['works_id']) for r in flagged) + '\n', encoding='utf-8')
        print(f"\n📝 일반문학 후보 works_id {len(flagged)}건 저장: {out}")


if __name__ == '__main__':
    main()
