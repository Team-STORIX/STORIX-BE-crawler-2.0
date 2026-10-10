"""HTTP 우선 수집 · 드라이버 주기 재시작 · 플랫폼 병렬 (#7)."""
import io
import json
import threading
import urllib.error

import pytest

pytest.importorskip('selenium')
from crawler import report  # noqa: E402
from crawler.output.jsonl_writer import JSONLWriter  # noqa: E402
from crawler.parallel import run_platforms  # noqa: E402
from modules.crawler.base_crawler import BaseCrawler, HttpUnavailable, RateLimitedError  # noqa: E402
from modules.crawler.naver_crawler import parse_naver_title_info  # noqa: E402
from modules.crawler.naver_novel_crawler import NaverNovelCrawler  # noqa: E402
from modules.crawler.naver_series_crawler import NaverSeriesCrawler  # noqa: E402
from modules.crawler.ridibooks_crawler import parse_ridi_book_page  # noqa: E402


@pytest.fixture(autouse=True)
def clean_report(monkeypatch):
    monkeypatch.setattr('modules.crawler.base_crawler.time.sleep', lambda s: None)
    report.reset()
    yield
    report.reset()


class Crawler(BaseCrawler):
    REPORT_PLATFORM = 'test'
    REQUEST_INTERVAL = 0.0
    RESTART_EVERY = 2

    def __init__(self, http=(), browser=()):
        super().__init__()
        self.http = list(http)
        self.browser = list(browser)
        self.opened = []
        self.restarts = 0

    def crawl_detail_http(self, url):
        step = self.http.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    def crawl_detail(self, url):
        self.opened.append(url)
        return self.browser.pop(0)

    def _restart_driver(self):
        self.restarts += 1


def test_http_result_skips_browser():
    c = Crawler(http=[{'works_name': 'x'}])
    assert c.crawl_detail_with_retry('u') == {'works_name': 'x'}
    assert c.opened == []
    assert report.summary()['by_platform'] == {'test': {'SUCCESS': 1}}


def test_http_unavailable_falls_back_to_browser():
    c = Crawler(http=[HttpUnavailable('형식 다름')], browser=[{'works_name': 'x'}])
    assert c.crawl_detail_with_retry('u') == {'works_name': 'x'}
    assert c.opened == ['u']


def test_http_none_is_not_found_without_retry():
    c = Crawler(http=[None])
    assert c.crawl_detail_with_retry('u') is None
    assert c.opened == []
    assert report.summary()['by_platform'] == {'test': {'DETAIL_NOT_FOUND': 1}}
    assert not report.failed()


def test_http_rate_limit_backs_off():
    c = Crawler(http=[RateLimitedError('HTTP 429'), {'works_name': 'x'}])
    assert c.crawl_detail_with_retry('u') == {'works_name': 'x'}
    assert c._interval >= 2.0


def test_use_http_false_uses_browser_only():
    c = Crawler(http=[{'works_name': 'http'}], browser=[{'works_name': 'browser'}])
    c.use_http = False
    assert c.crawl_detail_with_retry('u') == {'works_name': 'browser'}


def test_driver_restarts_every_n_browser_details():
    c = Crawler(browser=[{'n': i} for i in range(5)])
    c.use_http = False
    for i in range(5):
        c.crawl_detail_with_retry(f'u{i}')
    assert c.restarts == 2  # 2건마다: 3번째 · 5번째 직전


def test_naver_novel_and_series_do_not_use_webtoon_api():
    for cls in (NaverNovelCrawler, NaverSeriesCrawler):
        assert not cls()._has_http_detail()


@pytest.mark.parametrize('code,raised', [
    (404, None), (429, RateLimitedError), (500, HttpUnavailable), (403, HttpUnavailable),
])
def test_http_get_status_mapping(monkeypatch, code, raised):
    def urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, code, 'x', {}, io.BytesIO(b''))
    monkeypatch.setattr('modules.crawler.base_crawler.urllib.request.urlopen', urlopen)
    c = Crawler()
    if raised is None:
        assert c.http_get('https://x') is None
    else:
        with pytest.raises(raised):
            c.http_get('https://x')


def test_http_403_is_block_for_ridi(monkeypatch):
    def urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 403, 'x', {}, io.BytesIO(b''))
    monkeypatch.setattr('modules.crawler.base_crawler.urllib.request.urlopen', urlopen)
    c = Crawler()
    c.HTTP_403_IS_BLOCK = True
    with pytest.raises(RateLimitedError):
        c.http_get('https://x')


NAVER_INFO = {
    'titleName': '전지적 독자 시점',
    'webtoonLevelCode': 'WEBTOON',
    'communityArtists': [
        {'name': 'UMI', 'artistTypeList': ['ARTIST_WRITER']},
        {'name': '슬리피-C', 'artistTypeList': ['ARTIST_PAINTER']},
        {'name': '싱숑', 'artistTypeList': ['ARTIST_NOVEL_ORIGIN']},
    ],
    'synopsis': "'이건 내가 아는 그 전개다'\n세계가 멸망했다.",
    'age': {'type': 'RATE_15', 'description': '15세 이용가'},
    'curationTagList': [{'tagName': '판타지'}, {'tagName': '게임'}, {'tagName': '#헌터물'}],
    'sharedThumbnailUrl': 'https://shared-comic.pstatic.net/thumb/webtoon/747269/t.jpg',
}


def test_parse_naver_title_info():
    r = parse_naver_title_info(NAVER_INFO, 'https://comic.naver.com/webtoon/list?titleId=747269')
    assert r['works_name'] == '전지적 독자 시점'
    assert (r['author'], r['illustrator'], r['original_author']) == ('UMI', '슬리피-C', '싱숑')
    assert r['artist_name'] == 'UMI, 슬리피-C, 싱숑'
    assert r['genre'] == '판타지'  # 화면처럼 첫 태그가 장르
    assert r['hashtags'] == ['게임', '헌터물']
    assert r['age_classification'] == '15세 이용가'
    assert r['thumbnail_url'].startswith('https://shared-comic')
    assert r['works_type'] == '웹툰'


def test_parse_naver_writer_painter_same_person():
    info = {**NAVER_INFO, 'communityArtists': [{'name': '조석', 'artistTypeList': ['ARTIST_WRITER', 'ARTIST_PAINTER']}]}
    r = parse_naver_title_info(info, 'u')
    assert (r['author'], r['illustrator'], r['artist_name']) == ('조석', '조석', '조석')


def test_parse_naver_challenge_is_skipped():
    assert parse_naver_title_info({**NAVER_INFO, 'webtoonLevelCode': 'BEST_CHALLENGE'}, 'u') is None


def test_parse_naver_unexpected_shape_falls_back():
    with pytest.raises(HttpUnavailable):
        parse_naver_title_info({'error': 'x'}, 'u')


def _ridi_page(book_id='111', title='천관사복', categories=(('BL 웹툰', 'BL 웹툰'),), groups=None,
               intro='첫 줄 \r\n둘째 줄<br/>셋째 &amp; 끝', adult=False, notices=()):
    groups = groups if groups is not None else [
        {'title': '그림', 'authors': [{'name': 'STARember', 'role': 'ILLUSTRATOR'}]},
        {'title': '글', 'authors': [{'name': '백몽사', 'role': 'STORY_WRITER'}]},
        {'title': '원작', 'authors': [{'name': '묵향동후', 'role': 'ORIGINAL_AUTHOR'}]},
        {'title': '번역', 'authors': [{'name': '번역가', 'role': 'TRANSLATOR'}]},
    ]
    cells = [
        {'cell__BookDetailHomeHeader': {
            'thumbnail': {'cover': {'xxlarge': f'https://img.ridicdn.net/cover/{book_id}/xxlarge#1'}},
            'information': {
                'bookId': book_id,
                'title': {'title': title},
                'category': [{'parentCategory': {'name': p}, 'childCategory': {'name': c}} for p, c in categories],
                'authorGroups': groups,
            },
            'notice': {'notices': [{'title': n} for n in notices]},
        }},
        {'cell__BookDetailHomeBookIntroduction': {'introductionHtml': intro}},
        {'cell__BookDetailHomeKeywordTab': {'tabInfos': [{'name': '다정공'}, {'name': '별점1000개이상'}]}},
        {'cell__Panel': None},
    ]
    data = {'props': {'pageProps': {'sectionProps': {'gridQuery': {'riGrid': {'grid': {'cells': cells}}}}}}}
    book = json.dumps({'id': book_id, 'is_adult_only': adult})
    notice_json = json.dumps({'notices': [{'title': n} for n in notices]}, ensure_ascii=False)
    return (f'<html><head></head><body><script>{book}{notice_json}</script>'
            f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data, ensure_ascii=False)}</script>'
            '</body></html>')


def test_parse_ridi_book_page():
    r = parse_ridi_book_page(_ridi_page(), 'https://ridibooks.com/books/111')
    assert r['works_name'] == '천관사복'
    assert (r['author'], r['illustrator'], r['original_author']) == ('백몽사', 'STARember', '묵향동후')
    assert '번역가' not in r['artist_name']
    assert r['description'] == '첫 줄\n둘째 줄\n셋째 & 끝'
    assert (r['genre'], r['works_type']) == ('BL', '웹툰')
    assert r['age_classification'] == '전체연령가'
    assert r['hashtags'] == ['다정공']
    assert r['thumbnail_url'] == 'https://img.ridicdn.net/cover/111/xxlarge#1'


def test_parse_ridi_age_and_writer_painter():
    groups = [{'title': '글/그림', 'authors': [{'name': '희서', 'role': 'AUTHOR'}]}]
    r = parse_ridi_book_page(_ridi_page(groups=groups, adult=True, categories=(('로맨스 웹소설', '현대물'),)), 'u')
    assert (r['author'], r['illustrator']) == ('희서', '희서')
    assert r['age_classification'] == '18세 이용가'
    assert (r['genre'], r['works_type']) == ('로맨스', '웹소설')
    r15 = parse_ridi_book_page(_ridi_page(notices=('15세 이용가 안내',)), 'u')
    assert r15['age_classification'] == '15세 이용가'


def test_parse_ridi_general_lit_is_skipped():
    assert parse_ridi_book_page(_ridi_page(categories=(('일본 소설', '추리'),)), 'u') is None


def test_parse_ridi_without_next_data_falls_back():
    with pytest.raises(HttpUnavailable):
        parse_ridi_book_page('<html>로그인이 필요합니다</html>', 'u')


def test_writer_uses_platform_file(tmp_path):
    with JSONLWriter('ridibooks', 'search_titles', output_dir=tmp_path) as w:
        pass
    assert w.path.name == 'ridibooks_search_titles.jsonl'


def test_run_platforms_parallel_isolates_failures():
    done, lock = [], threading.Lock()

    def fn(p):
        if p == 'bad':
            raise RuntimeError('드라이버 시작 실패')
        with lock:
            done.append(p)

    run_platforms(['a', 'bad', 'b'], fn, parallel=3)
    assert sorted(done) == ['a', 'b']
    assert report.summary()['by_platform'] == {'bad': {'PARSING_FAILED': 1}}
    assert report.failed()


def test_run_platforms_records_rate_limit():
    def fn(p):
        raise RateLimitedError('403')

    run_platforms(['ridibooks'], fn)
    assert report.summary()['by_platform'] == {'ridibooks': {'RATE_LIMITED': 1}}
