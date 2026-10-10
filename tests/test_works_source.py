"""작품별 수집 이력 · 링크 자동 복구 · 변경 감지 (#28).

MySQL 테스트는 REVIEW_TEST_MYSQL_HOST 가 있을 때만 돈다 (test_review.py 와 같은 조건).
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from review.landing import platform_work_key
from review.store import content_fingerprint
from test_review import CATALOG, item, needs_mysql, store  # noqa: F401  (store 는 fixture)


@pytest.mark.parametrize('url,key', [
    ('https://comic.naver.com/webtoon/list?titleId=747269&page=2', ('NAVER_WEBTOON', '747269')),
    ('https://series.naver.com/comic/detail.series?productNo=9165599', ('NAVER_SERIES', '9165599')),
    ('https://page.kakao.com/content/61343487/', ('KAKAO_PAGE', '61343487')),
    ('https://ridibooks.com/books/4928000826?_rdt_sid=x', ('RIDIBOOKS', '4928000826')),
    ('https://example.com/a', None),
])
def test_platform_work_key(url, key):
    assert platform_work_key(url) == key


def test_fingerprint_ignores_crawl_time():
    a = item(crawled_at='2026-10-09T00:00:00Z', mode='initial')
    b = item(crawled_at='2026-10-10T00:00:00Z', mode='update_fields')
    assert content_fingerprint(a) == content_fingerprint(b)
    assert content_fingerprint(a) != content_fingerprint({**a, 'description': '바뀐 소개'})


# ---------------------------------------------------------------- 링크 복구 (크롤러 없이)

pytest.importorskip('selenium')
from crawler.modes.recover import _recover_one, matches_source  # noqa: E402

SOURCE = {
    'source_url': 'https://ridibooks.com/books/1',
    'title_snapshot': '천관사복',
    'artist_snapshot': '백몽사, STARember, 묵향동후',
    'works_type': '웹툰',
    'search_keywords': ['천관사복'],
}


def detail(**kw):
    return {'works_name': '천관사복', 'artist_name': 'STARember, 백몽사', 'works_type': '웹툰',
            'source_url': 'https://ridibooks.com/books/2', **kw}


@pytest.mark.parametrize('result,ok', [
    (detail(), True),
    (detail(works_name='천관사복 [완결]'), True),                 # 라벨은 무시
    (detail(works_name='천관사복 외전 모음'), False),               # 다른 작품
    (detail(works_type='웹소설'), False),                         # 같은 제목 다른 판
    (detail(artist_name='다른작가'), False),
])
def test_matches_source(result, ok):
    assert matches_source(SOURCE, '천관사복', result) is ok


class FakeCrawler:
    def __init__(self, details, candidates):
        self.details = details
        self.candidates = candidates
        self.opened = []

    def crawl_detail_with_retry(self, url):
        self.opened.append(url)
        return self.details.get(url)

    def search_candidates(self, title):
        return self.candidates


def cand(url, text='천관사복', hint='웹툰'):
    return {'url': url, 'text': text, 'type_hint': hint, 'kind': '정확'}


def test_recover_reopens_old_link_first():
    c = FakeCrawler({'https://ridibooks.com/books/1': detail(source_url='https://ridibooks.com/books/1')}, [])
    assert _recover_one(c, SOURCE)[0] == 'REOPENED'
    assert c.opened == ['https://ridibooks.com/books/1']


def test_recover_finds_new_link_matching_snapshot():
    c = FakeCrawler(
        {'https://ridibooks.com/books/3': detail(artist_name='남'),  # 작가가 달라 거른다
         'https://ridibooks.com/books/2': detail()},
        [cand('https://ridibooks.com/books/1'), cand('https://ridibooks.com/books/3'),
         cand('https://ridibooks.com/books/2'), cand('https://ridibooks.com/books/9', hint='웹소설')],
    )
    outcome, result = _recover_one(c, SOURCE)
    assert outcome == 'RELINKED' and result['source_url'] == 'https://ridibooks.com/books/2'
    # 옛 링크는 처음 한 번만 열고, 다른 유형 힌트 후보는 열지 않는다
    assert c.opened == ['https://ridibooks.com/books/1', 'https://ridibooks.com/books/3',
                        'https://ridibooks.com/books/2']


def test_recover_not_found():
    c = FakeCrawler({}, [cand('https://ridibooks.com/books/5')])
    assert _recover_one(c, SOURCE) == ('NOT_FOUND', None)


# ---------------------------------------------------------------- 저장소 (MySQL)

URL = 'https://ridibooks.com/books/4928000826'


def source_row(store, url=URL):  # noqa: F811
    return store.get_source(url)


@needs_mysql
def test_load_records_source_and_import_links_works(store):  # noqa: F811
    from review import service
    service.load_run(store, CATALOG, [item(source_url=URL + '?x=1', platform='RIDIBOOKS', works_type='웹툰')], 't')
    row = source_row(store)
    assert (row['platform'], row['platform_work_id'], row['source_url']) == ('RIDIBOOKS', '4928000826', URL)
    assert row['crawl_status'] == 'SUCCESS' and row['works_type'] == '웹툰'
    assert row['search_keywords'] == ['전지적 독자 시점']
    assert len(row['fingerprint_hash']) == 64

    sid = store.queue('AUTO_PASS')[0]['id']
    store.mark_imported(sid, 777)
    assert source_row(store)['works_id'] == 777


@needs_mysql
def test_title_change_keeps_old_title_as_keyword(store):  # noqa: F811
    from review import service
    raw = item(source_url=URL, platform='RIDIBOOKS')
    service.load_run(store, CATALOG, [raw], 't')
    service.load_run(store, CATALOG, [{**raw, 'works_name': '전독시'}], 't')
    assert source_row(store)['search_keywords'] == ['전지적 독자 시점', '전독시']


@needs_mysql
def test_recrawl_with_same_content_is_not_sent_again(store):  # noqa: F811
    from review import service
    raw = item(source_url=URL, platform='RIDIBOOKS', crawled_at='2026-10-09T00:00:00Z')
    service.load_run(store, CATALOG, [raw], 't')
    sid = store.queue('AUTO_PASS')[0]['id']
    store.mark_imported(sid, 1)

    service.load_run(store, CATALOG, [{**raw, 'crawled_at': '2026-10-10T00:00:00Z'}], 't')
    assert store.get(sid)['status'] == 'IMPORTED'
    assert store.importable() == []

    service.load_run(store, CATALOG, [{**raw, 'description': '바뀐 소개'}], 't')
    assert store.get(sid)['status'] == 'AUTO_PASS'


@needs_mysql
def test_report_failures_counted_once_and_repeated_failure_goes_to_review(store, tmp_path):  # noqa: F811
    from review import service
    service.load_run(store, CATALOG, [item(source_url=URL, platform='RIDIBOOKS')], 't')
    sid = store.queue('AUTO_PASS')[0]['id']
    store.mark_imported(sid, 1)

    def write_report(at):
        path = tmp_path / 'update_fields_report.json'
        path.write_text(json.dumps({'failures': [
            {'platform': 'ridibooks', 'status': 'DETAIL_NOT_FOUND', 'target': URL + '?a=b', 'detail': '',
             'at': at.isoformat(timespec='seconds')},
            {'platform': 'ridibooks', 'status': 'SEARCH_FAILED', 'target': '없는 작품', 'detail': '', 'at': at.isoformat()},
        ]}), encoding='utf-8')
        return path

    now = datetime.now(timezone.utc) + timedelta(minutes=1)
    path = write_report(now)
    assert service.record_report_failures(store, path) == {'failures': 1, 'link_broken': 0}
    service.record_report_failures(store, path)  # 같은 리포트를 다시 읽어도 한 번만
    assert source_row(store)['crawl_fail_count'] == 1
    assert [s['source_url'] for s in store.broken_sources()] == [URL]

    for i in (2, 3):
        service.record_report_failures(store, write_report(now + timedelta(hours=i)))
    row = source_row(store)
    assert (row['crawl_status'], row['crawl_fail_count']) == ('DETAIL_NOT_FOUND', 3)
    staged = store.get(sid)
    assert staged['status'] == 'NEEDS_REVIEW'
    assert staged['violations'][-1]['code'] == 'LINK_BROKEN'


@needs_mysql
def test_failure_for_unknown_link_creates_row_and_relink(store):  # noqa: F811
    assert store.record_source_failure('https://page.kakao.com/content/1/', 'DETAIL_NOT_FOUND', 'x') == 1
    store.mark_source_relinked('https://page.kakao.com/content/1', 'https://page.kakao.com/content/2')
    row = store.get_source('https://page.kakao.com/content/1')
    assert (row['crawl_status'], row['relinked_to']) == ('RELINKED', 'https://page.kakao.com/content/2')
    assert store.broken_sources() == []


def test_report_for(tmp_path):
    from review.service import report_for
    (tmp_path / 'search_titles_report.json').write_text('{}')
    (tmp_path / 'initial_report.json').write_text('{}')
    assert report_for(tmp_path / 'ridibooks_search_titles.jsonl').name == 'search_titles_report.json'
    assert report_for(tmp_path / 'kakao_page_initial.jsonl').name == 'initial_report.json'
    assert report_for(tmp_path / 'ridibooks_recover.jsonl') is None

