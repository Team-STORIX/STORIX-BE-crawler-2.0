"""레진코믹스 크롤러 (#59).

작품 페이지(https://www.lezhin.com/ko/comic/{alias}) HTML 안의 Next.js 데이터(self.__next_f)에 작품 정보가 통째로 있다.
성인 작품은 성인 인증된 로그인 세션 쿠키가 있어야 열린다 (없으면 로그인 화면으로 이동).
세션은 `python cli.py login --platform lezhin` 으로 사람이 브라우저에서 로그인해 sessions/ 에 저장한다.
"""
import json
import pickle
import re
import time

from .base_crawler import PASS_THROUGH, BaseCrawler, HttpUnavailable, SessionExpiredError
from config import LEZHIN_COOKIE_FILE

SITE = 'https://www.lezhin.com'
LOGIN_URL = SITE + '/ko/login'
_ALIAS = re.compile(r'lezhin\.com/(?:ko/)?comic/([^/?#\s]+)')
_FLIGHT = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', re.S)
# 세로 표지 600x800. 페이지 og:image 의 wide.jpg 는 가로 배너(1200x600)라 쓰지 않는다
COVER = 'https://ccdn.lezhin.com/v2/comics/{id}/images/tall.jpg'

# 레진 장르 코드 → BE 장르 표기. 모르는 코드는 그대로 둬 검수에서 걸리게 한다
LEZHIN_GENRES = {
    'bl': 'BL', 'romance': '로맨스', 'drama': '드라마', 'fantasy': '판타지', 'gag': '개그', 'action': '액션',
    'mystery': '스릴러', 'thriller': '스릴러', 'horror': '스릴러', 'sports': '스포츠', 'day': '일상', 'daily': '일상',
    'historical': '무협', 'martial': '무협', 'gl': 'GL', 'school': '학원',
}
LEZHIN_AGES = {'19': '18세 이용가', '18': '18세 이용가', '15': '15세 이용가', '12': '12세 이용가',
               'all': '전체연령가', '0': '전체연령가'}
# 작가 역할. writer 는 글 · 그림을 같이 한 작가, scripter 는 글, painter 는 그림.
# label(스튜디오) · publisher(출판사)는 작가가 아니라 넣지 않는다
_ROLES = {'writer': 'author', 'scripter': 'author', 'painter': 'illustrator', 'original': 'original_author'}
# 레진 이벤트 · 순위 · 분류 태그와 영문 표기 태그는 해시태그로 쓰지 않는다
#   월드드랍 · WorldDrop · 여성인기19 · 남성인기19 · 스낵타임 · 후방주의
LEZHIN_META_TAGS = re.compile(r'^(?:월드드랍|스낵타임|후방주의|.*인기\d*|[A-Za-z0-9 _-]+)$')


def lezhin_alias(url: str) -> str | None:
    m = _ALIAS.search(url or '')
    return m.group(1).strip() if m else None


def lezhin_content(src: str) -> dict | None:
    """작품 페이지 HTML → content 객체. 데이터가 없으면 None."""
    chunks = _FLIGHT.findall(src or '')
    if not chunks:
        return None
    flight = ''.join(json.loads(f'"{c}"') for c in chunks)
    i = flight.find('{"content":{"id":')
    if i < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(flight, i)
    except ValueError:
        return None
    return obj.get('content')


def parse_lezhin_content(c: dict, url: str) -> dict:
    display = c.get('display') or {}
    roles = {'author': '', 'illustrator': '', 'original_author': ''}
    names = []
    for a in c.get('artists') or []:
        name = (a.get('name') or '').strip()
        field = _ROLES.get(a.get('role'))
        if not name or not field:
            continue
        if name not in names:
            names.append(name)
        if not roles[field]:
            roles[field] = name
    writer = next((a['name'].strip() for a in c.get('artists') or [] if a.get('role') == 'writer'), '')
    if writer and not roles['illustrator']:
        roles['illustrator'] = writer  # 글 · 그림을 같이 한 작가
    codes = c.get('genres') or []
    genre = LEZHIN_GENRES.get(codes[0], codes[0]) if codes else ''
    tags = [t.strip() for t in (c.get('properties') or {}).get('tags') or [] if isinstance(t, str) and t.strip()]
    hashtags = list(dict.fromkeys(t for t in tags if not LEZHIN_META_TAGS.match(t)))
    rating = str(c.get('rating') or '').strip()
    return {
        'platform': 'LEZHIN',
        'works_name': (display.get('title') or '').strip(),
        'artist_name': ', '.join(names),
        **roles,
        'age_classification': LEZHIN_AGES.get(rating, '18세 이용가' if c.get('isAdult') else ''),
        'description': (display.get('synopsis') or '').strip(),
        'genre': genre,
        'hashtags': hashtags,
        'thumbnail_url': COVER.format(id=c['id']) if c.get('id') else '',
        'works_type': '웹툰',
        'source_url': f"{SITE}/ko/comic/{c.get('alias') or lezhin_alias(url)}",
    }


class LezhinCrawler(BaseCrawler):
    REPORT_PLATFORM = 'lezhin'
    LOGIN_URL_MARKERS = ('/login',)
    REQUEST_INTERVAL = 1.0

    def _cookie_header(self) -> str:
        if not LEZHIN_COOKIE_FILE.exists():
            return ''
        try:
            cookies = pickle.load(open(LEZHIN_COOKIE_FILE, 'rb'))
        except Exception:
            return ''
        return '; '.join(f"{c['name']}={c['value']}" for c in cookies if 'lezhin' in (c.get('domain') or ''))

    def crawl_detail_http(self, url: str) -> dict | None:
        alias = lezhin_alias(url)
        if not alias:
            raise HttpUnavailable('작품 주소 아님')
        cookie = self._cookie_header()
        if not cookie:
            raise HttpUnavailable('로그인 세션 없음')
        src = self.http_get(f'{SITE}/ko/comic/{alias}', headers={'Cookie': cookie})
        if src is None:
            return None
        content = lezhin_content(src)
        if content is None:
            # 로그인 화면으로 돌아왔거나(세션 만료 · 성인 인증 안 됨) 화면 구조가 바뀌었다
            raise HttpUnavailable('작품 데이터 없음 (세션 만료 의심)')
        return parse_lezhin_content(content, url)

    def crawl_detail(self, url: str) -> dict | None:
        alias = lezhin_alias(url)
        if not alias:
            return None
        try:
            self.driver.get(f'{SITE}/ko/comic/{alias}')
            if self.is_login_page():
                raise SessionExpiredError(f'레진 로그인 화면으로 이동: {url}')
            content = lezhin_content(self.driver.page_source)
            return parse_lezhin_content(content, url) if content else None
        except PASS_THROUGH:
            raise
        except Exception as e:
            self._log.warning('crawl_detail 실패 (%s): %s', url, e)
            return None

    # ---------------------------------------------------------------- 로그인

    def _http_adult_ok(self) -> bool:
        try:
            return self.crawl_detail_http(f'{SITE}/ko/comic/jinx') is not None
        except HttpUnavailable:
            return False

    def login_with_cookies(self) -> bool:
        if not LEZHIN_COOKIE_FILE.exists():
            return False
        try:
            self.driver.get(SITE)
            for c in pickle.load(open(LEZHIN_COOKIE_FILE, 'rb')):
                c.pop('expiry', None)
                try:
                    self.driver.add_cookie(c)
                except Exception:
                    pass
            self.driver.get(f'{SITE}/ko/comic/jinx')
            return not self.is_login_page() and lezhin_content(self.driver.page_source) is not None
        except Exception as e:
            print(f'⚠️ 레진 쿠키 로그인 실패: {e}')
            return False

    def login(self) -> bool:
        if self._http_adult_ok():
            print('✅ [레진] 저장된 세션으로 성인 작품 열림')
            return True
        if self.login_with_cookies():
            return True
        import os
        if os.environ.get('DOCKER_ENV') == 'true':
            return False
        print('\n' + '=' * 60)
        print('🔐 브라우저 창에서 레진코믹스 로그인을 완료하세요 (성인 인증된 계정). 최대 5분')
        print('=' * 60 + '\n')
        self.driver.get(LOGIN_URL)
        deadline = time.time() + 300
        while time.time() < deadline:
            time.sleep(4)
            if self.is_login_page():
                continue
            pickle.dump(self.driver.get_cookies(), open(LEZHIN_COOKIE_FILE, 'wb'))
            if self._http_adult_ok():
                print('✅ [레진] 로그인 · 성인 작품 확인. 쿠키를 저장했습니다.')
                return True
        print('⏰ 레진 로그인 대기 시간 초과 (성인 인증이 안 된 계정이면 성인 작품이 열리지 않습니다).')
        return False

    def search_url_by_title(self, title: str) -> str | None:
        return None
