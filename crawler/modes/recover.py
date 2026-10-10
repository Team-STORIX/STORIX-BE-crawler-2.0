"""recover 모드: 깨진 작품 링크를 다시 찾는다 (#28).

수집 이력(works_source)에서 마지막 상세 수집이 실패한 작품을 골라
  1) 저장된 링크를 한 번 더 연다 (일시적인 실패일 수 있다)
  2) 저장된 검색어(지금까지 수집된 제목들)로 검색해, 후보 중 제목 · 작가 · 작품 유형이 이력과 맞는 것을 연다
찾은 작품은 ./output/YYYY-MM-DD/<플랫폼>_recover.jsonl 에 쓴다. 검수 적재하면 새 링크가 이력에 들어가고,
BE 는 같은 작품으로 판정해 링크를 바꾼다. 못 찾으면 SEARCH_FAILED 로 실패 횟수를 늘리고, 반복되면 검수 대기로 간다.

    python cli.py stage recover --env dev [--platform ridibooks] [--limit 50]
    python cli.py stage load --env dev --input './output/YYYY-MM-DD/*_recover.jsonl' --source recover
"""
from crawler import report
from crawler.modes.search_titles import _CRAWLER_MAP
from crawler.output.jsonl_writer import JSONLWriter
from modules.crawler.base_crawler import AuthExpiredError, BaseCrawler, RateLimitedError, pick_candidates
from review.artists import normalize_artists
from review.landing import canonical_landing_url

# 이력의 플랫폼(BE enum name) → 크롤러
PLATFORM_KEYS = {
    'NAVER_WEBTOON': 'naver_webtoon',
    'NAVER_NOVEL': 'naver_novel',
    'NAVER_SERIES': 'naver_series',
    'KAKAO_PAGE': 'kakao_page',
    'RIDIBOOKS': 'ridibooks',
}
# 검색어 하나에서 열어 볼 후보 수
MAX_CANDIDATES = 3


def _names(artist_name: str) -> set[str]:
    return {''.join(n.split()) for n in (artist_name or '').split(',') if n.strip()}


def matches_source(source: dict, keyword: str, result: dict) -> bool:
    """찾은 작품이 이력의 작품과 같은지: 제목이 검색어와 맞고, 작품 유형이 같고, 작가가 한 명 이상 겹친다.
    이력에 작품 유형 · 작가가 없으면 그 조건은 건너뛴다."""
    if BaseCrawler.title_match(keyword, result.get('works_name') or '') is None:
        return False
    want_type = source.get('works_type')
    if want_type and (result.get('works_type') or '').strip() != want_type:
        return False
    known = _names(source.get('artist_snapshot'))
    return not known or bool(known & _names(normalize_artists(result)['artist_name']))


def _recover_one(crawler, source: dict) -> tuple[str, dict | None]:
    """(결과, 수집 결과). 결과 = REOPENED(옛 링크가 다시 열림) | RELINKED(새 링크) | NOT_FOUND."""
    url = source['source_url']
    result = crawler.crawl_detail_with_retry(url)
    if result:
        return 'REOPENED', result

    keywords = source.get('search_keywords') or ([source['title_snapshot']] if source.get('title_snapshot') else [])
    for keyword in keywords:
        candidates = pick_candidates(crawler.search_candidates(keyword), source.get('works_type'))
        for c in candidates[:MAX_CANDIDATES]:
            if canonical_landing_url(c['url']) == url:
                continue  # 방금 실패한 그 링크
            print(f"   ↳ 후보 [{keyword}] {c['url']} [{c['text']}]")
            found = crawler.crawl_detail_with_retry(c['url'])
            if found and matches_source(source, keyword, found):
                return 'RELINKED', found
    return 'NOT_FOUND', None


def run_recover(store, platform: str | None = None, limit: int = 50) -> dict:
    """platform: BE enum name (예: RIDIBOOKS). 생략하면 전 플랫폼."""
    sources = store.broken_sources(platform, limit)
    summary = {'targets': len(sources), 'REOPENED': 0, 'RELINKED': 0, 'NOT_FOUND': 0, 'files': []}
    if not sources:
        print('복구할 링크가 없습니다.')
        return summary

    by_platform: dict[str, list[dict]] = {}
    for s in sources:
        if s['platform'] in PLATFORM_KEYS:
            by_platform.setdefault(s['platform'], []).append(s)

    for enum_name, rows in by_platform.items():
        key = PLATFORM_KEYS[enum_name]
        CrawlerClass, label = _CRAWLER_MAP[key]
        print(f'\n🩹 [{label}] 깨진 링크 {len(rows)}건 복구 시작')
        crawler = CrawlerClass()
        crawler.start_driver()
        try:
            if not crawler.login():
                report.record(key, report.AUTH_EXPIRED, '로그인', f'{label} 로그인 실패')
                print(f'⚠️  [{label}] 로그인 실패 — 건너뜀')
                continue
            with JSONLWriter(key, 'recover') as writer:
                for i, source in enumerate(rows, 1):
                    print(f"\n  [{i}/{len(rows)}] {source.get('title_snapshot') or '(제목 없음)'} — {source['source_url']}")
                    try:
                        outcome, result = _recover_one(crawler, source)
                    except (RateLimitedError, AuthExpiredError) as e:
                        print(f'⚠️  [{label}] 중단: {e}')
                        break
                    summary[outcome] += 1
                    if outcome == 'NOT_FOUND':
                        fails = store.record_source_failure(source['source_url'], report.SEARCH_FAILED,
                                                            '링크 복구 실패: 검색 결과에 같은 작품 없음')
                        print(f'  ❌ 못 찾음 (연속 실패 {fails}회)')
                        continue
                    writer.write(result)
                    if outcome == 'RELINKED':
                        new_url = canonical_landing_url(result['source_url'])
                        store.mark_source_relinked(source['source_url'], new_url)
                        print(f'  ✅ 새 링크: {new_url}')
                    else:
                        print('  ✅ 옛 링크가 다시 열림')
            if writer.count:
                summary['files'].append(str(writer.path))
        finally:
            crawler.close_driver()
    return summary
