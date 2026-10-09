"""검수 파이프라인 테스트.

룰·카탈로그·import 클라이언트는 DB 없이 돈다.
StagingStore 테스트는 REVIEW_TEST_MYSQL_HOST 가 있을 때만 돈다 (빈 MySQL 하나면 된다).
"""
import os

import pytest

from review.catalog import EnumCatalog
from review.importer import run_import, to_request_item
from review.rules import AUTO_PASS, NEEDS_REVIEW, REJECTED, check_run, validate_item

CATALOG = EnumCatalog({
    'platform': {'storedAs': 'name', 'values': [
        {'name': 'NAVER_WEBTOON', 'dbValue': '네이버 웹툰'}, {'name': 'RIDIBOOKS', 'dbValue': '리디북스'}]},
    'genre': {'storedAs': 'dbValue', 'values': [
        {'name': 'HISTORICAL', 'dbValue': '무협'}, {'name': 'FANTASY', 'dbValue': '판타지'},
        {'name': 'ROFAN', 'dbValue': '로판'}, {'name': 'BL', 'dbValue': 'BL'}]},
    'ageClassification': {'storedAs': 'dbValue', 'values': [
        {'name': 'ALL', 'dbValue': '전체연령가'}, {'name': 'AGE_15', 'dbValue': '15세 이용가'},
        {'name': 'AGE_18', 'dbValue': '18세 이용가'}]},
    'worksType': {'storedAs': 'dbValue', 'values': [
        {'name': 'WEBTOON', 'dbValue': '웹툰'}, {'name': 'WEBNOVEL', 'dbValue': '웹소설'}]},
})


def item(**overrides) -> dict:
    base = {
        'platform': 'NAVER_WEBTOON',
        'works_name': '전지적 독자 시점',
        'artist_name': '슬리피-C, 싱숑',
        'author': '싱숑',
        'illustrator': '슬리피-C',
        'original_author': '',
        'age_classification': '15세 이용가',
        'description': '오직 나만이 이 세계의 결말을 알고 있다.',
        'genre': '판타지',
        'hashtags': ['회귀', ' 먼치킨 ', ''],
        'thumbnail_url': 'https://image-comic.pstatic.net/x.jpg',
        'works_type': '웹툰',
        'source_url': 'https://comic.naver.com/webtoon/list?titleId=747269',
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------- catalog

def test_catalog_resolves_name_and_db_value():
    assert CATALOG.resolve('genre', '무협') == 'HISTORICAL'
    assert CATALOG.resolve('genre', 'historical') == 'HISTORICAL'
    assert CATALOG.resolve('ageClassification', '15세이용가') == 'AGE_15'
    assert CATALOG.resolve('platform', 'NAVER_WEBTOON') == 'NAVER_WEBTOON'
    assert CATALOG.resolve('genre', '무협/사극') is None
    assert CATALOG.resolve('genre', '') is None
    assert CATALOG.stored_as['platform'] == 'name'


def test_catalog_requires_every_kind():
    with pytest.raises(ValueError):
        EnumCatalog({'genre': {'storedAs': 'dbValue', 'values': [{'name': 'BL', 'dbValue': 'BL'}]}})


def test_catalog_unwraps_custom_response(tmp_path):
    import json
    from review.catalog import load_catalog_file
    path = tmp_path / 'catalog.json'
    path.write_text(json.dumps({'isSuccess': True, 'result': CATALOG.raw}), encoding='utf-8')
    assert load_catalog_file(path).resolve('genre', '로판') == 'ROFAN'


# ---------------------------------------------------------------- Layer 1

def test_clean_item_auto_passes_with_enum_names():
    v = validate_item(item(), CATALOG)
    assert v.status == AUTO_PASS
    assert v.violations == []
    assert v.normalized['genre'] == 'FANTASY'
    assert v.normalized['age_classification'] == 'AGE_15'
    assert v.normalized['works_type'] == 'WEBTOON'
    assert v.normalized['hashtags'] == ['회귀', '먼치킨']


@pytest.mark.parametrize('field', ['works_name', 'artist_name', 'source_url'])
def test_missing_identity_is_rejected(field):
    v = validate_item(item(**{field: '  '}), CATALOG)
    assert v.status == REJECTED
    assert {'field': field, 'code': 'MISSING', 'value': None, 'severity': REJECTED} in v.violations


def test_unknown_platform_is_rejected():
    assert validate_item(item(platform='BOMTOON2'), CATALOG).status == REJECTED


def test_invalid_url_is_rejected():
    assert validate_item(item(source_url='comic.naver.com/x'), CATALOG).status == REJECTED


@pytest.mark.parametrize('field,value,code', [
    ('genre', '무협/사극', 'UNKNOWN_ENUM'),
    ('genre', '', 'MISSING'),
    ('age_classification', '', 'MISSING'),
    ('works_type', '만화책', 'UNKNOWN_ENUM'),
    ('description', '', 'MISSING'),
    ('thumbnail_url', '', 'MISSING'),
])
def test_fixable_problems_need_review(field, value, code):
    v = validate_item(item(**{field: value}), CATALOG)
    assert v.status == NEEDS_REVIEW
    assert [x['code'] for x in v.violations if x['field'] == field] == [code]


def test_length_limits_follow_works_columns():
    v = validate_item(item(author='가' * 101, thumbnail_url='https://x/' + 'a' * 500), CATALOG)
    assert v.status == NEEDS_REVIEW
    assert {x['field'] for x in v.violations} == {'author', 'thumbnail_url'}


def test_non_string_values_do_not_crash():
    v = validate_item(item(description=None, hashtags=None, genre=3), CATALOG)
    assert v.status == NEEDS_REVIEW
    assert v.normalized['hashtags'] == []


# ---------------------------------------------------------------- Layer 1.5

def test_breaker_catches_the_measured_broken_run():
    # 로드맵 실측: 20/20 작가 빈 값, 장르 "장르&", 연령 전부 18세
    broken = [item(artist_name='', genre='장르&', age_classification='18세 이용가',
                   source_url=f'https://x/{i}') for i in range(20)]
    reasons = check_run(broken, prev_count=None)
    assert len(reasons) == 3


def test_breaker_passes_healthy_run():
    ages = ['전체연령가', '15세 이용가', '18세 이용가']
    genres = ['판타지', '무협', '로판']
    healthy = [item(age_classification=ages[i % 3], genre=genres[i % 3]) for i in range(30)]
    assert check_run(healthy, prev_count=28) == []


def test_breaker_skips_distribution_on_small_runs():
    small = [item(artist_name='') for _ in range(5)]
    assert check_run(small) == []


def test_breaker_flags_count_swing():
    assert check_run([item()] * 3, prev_count=10)
    assert check_run([item()] * 16, prev_count=10)
    assert not check_run([item()] * 15, prev_count=10)


# ---------------------------------------------------------------- import client

class FakeStore:
    def __init__(self, rows):
        self.rows = rows
        self.imported, self.failed = {}, {}

    def importable(self, limit):
        return self.rows[:limit]

    def mark_imported(self, sid, works_id):
        self.imported[sid] = works_id

    def mark_import_failed(self, sid, error):
        self.failed[sid] = error


class FakeClient:
    def __init__(self, respond):
        self.respond = respond
        self.calls = []

    def import_works(self, items):
        self.calls.append(items)
        return self.respond(items)


def _rows(n):
    return [{'id': i, 'normalized': validate_item(item(), CATALOG).normalized} for i in range(1, n + 1)]


def test_request_item_uses_be_field_names():
    req = to_request_item(7, validate_item(item(original_author=''), CATALOG).normalized)
    assert req['stagingId'] == 7
    assert req['worksName'] == '전지적 독자 시점'
    assert req['genre'] == 'FANTASY'
    assert req['originalAuthor'] is None
    assert req['hashtags'] == ['회귀', '먼치킨']


def test_import_marks_per_item_results_and_chunks():
    store = FakeStore(_rows(150))

    def respond(items):
        out = []
        for it in items:
            if it['stagingId'] == 3:
                out.append({'stagingId': 3, 'result': 'FAILED', 'error': 'genre 변환 실패'})
            elif it['stagingId'] != 4:  # 4 는 응답에서 빠짐
                out.append({'stagingId': it['stagingId'], 'result': 'CREATED', 'worksId': 1000 + it['stagingId']})
        return out

    client = FakeClient(respond)
    summary = run_import(store, client)

    assert [len(c) for c in client.calls] == [100, 50]
    assert summary == {'requested': 150, 'imported': 148, 'failed': 2}
    assert store.imported[1] == 1001
    assert store.failed[3] == 'genre 변환 실패'
    assert 'BE 응답에 결과 없음' in store.failed[4]


def test_import_backend_down_fails_chunk_without_raising():
    import urllib.error
    store = FakeStore(_rows(3))

    def down(_items):
        raise urllib.error.URLError('connection refused')

    summary = run_import(store, FakeClient(down))
    assert summary == {'requested': 3, 'imported': 0, 'failed': 3}
    assert set(store.failed) == {1, 2, 3}


# ---------------------------------------------------------------- store (MySQL)

needs_mysql = pytest.mark.skipif(not os.getenv('REVIEW_TEST_MYSQL_HOST'), reason='REVIEW_TEST_MYSQL_HOST 없음')


@pytest.fixture
def store(monkeypatch):
    import config
    monkeypatch.setitem(config.MYSQL_CONFIG, 'host', os.environ['REVIEW_TEST_MYSQL_HOST'])
    monkeypatch.setitem(config.MYSQL_CONFIG, 'port', int(os.getenv('REVIEW_TEST_MYSQL_PORT', '3306')))
    monkeypatch.setitem(config.MYSQL_CONFIG, 'user', 'root')
    monkeypatch.setitem(config.MYSQL_CONFIG, 'password', os.getenv('REVIEW_TEST_MYSQL_PASSWORD', ''))

    from review import store as store_mod
    store_mod.ensure_schema()
    conn = store_mod.connect()
    cur = conn.cursor()
    for t in ('review_decision', 'works_staging', 'staging_run'):
        cur.execute(f'DELETE FROM {t}')
    conn.commit()
    yield store_mod.StagingStore(conn)
    conn.close()


@needs_mysql
def test_store_load_review_import_flow(store):
    from review import service

    items = [
        item(source_url='https://x/1'),
        item(source_url='https://x/2', genre='무협/사극'),
        item(source_url='https://x/3', works_name=''),
    ]
    result = service.load_run(store, CATALOG, items, 'naver_test')
    assert result['by_status'] == {AUTO_PASS: 1, NEEDS_REVIEW: 1, REJECTED: 1}
    assert result['held'] is False

    queue = store.queue(NEEDS_REVIEW)
    assert [q['source_url'] for q in queue] == ['https://x/2']
    sid = queue[0]['id']

    # 고친 값이 여전히 틀리면 승인 안 됨
    with pytest.raises(service.ReviewError):
        service.approve(store, CATALOG, sid, {'genre': '사극'}, 'tester')

    approved = service.approve(store, CATALOG, sid, {'genre': '무협'}, 'tester')
    assert approved['normalized']['genre'] == 'HISTORICAL'

    cur = store._cursor()
    cur.execute('SELECT field, raw_value, decided_value FROM review_decision')
    assert cur.fetchall() == [{'field': 'genre', 'raw_value': '무협/사극', 'decided_value': 'HISTORICAL'}]

    assert {r['id'] for r in store.importable()} == {sid, store.queue(AUTO_PASS)[0]['id']}

    store.mark_imported(sid, 99)
    stats = store.stats()
    assert stats['by_status'] == {AUTO_PASS: 1, REJECTED: 1, 'IMPORTED': 1}
    assert stats['review_decisions'] == 1


@needs_mysql
def test_store_recrawl_keeps_human_decision_when_raw_unchanged(store):
    from review import service

    raw = item(source_url='https://x/9', genre='무협/사극')
    service.load_run(store, CATALOG, [raw], 'naver_test')
    sid = store.queue(NEEDS_REVIEW)[0]['id']
    service.approve(store, CATALOG, sid, {'genre': 'HISTORICAL'}, 'tester')

    service.load_run(store, CATALOG, [dict(raw)], 'naver_test')
    assert store.get(sid)['status'] == 'APPROVED'

    # 원문이 바뀌면 다시 판정
    service.load_run(store, CATALOG, [{**raw, 'genre': '판타지'}], 'naver_test')
    row = store.get(sid)
    assert row['status'] == AUTO_PASS
    assert row['reviewed_by'] is None


@needs_mysql
def test_held_run_is_not_importable_until_released(store):
    from review import service

    broken = [item(source_url=f'https://x/b{i}', age_classification='18세 이용가', genre='판타지')
              for i in range(20)]
    result = service.load_run(store, CATALOG, broken, 'ridi_bl')
    assert result['held'] is True
    assert store.importable() == []

    assert store.release_run(result['run_id'], 'tester') is True
    assert len(store.importable()) == 20
