"""미스터블루 크롤러 (#59).

작품 페이지(https://www.mrblue.com/{webtoon|comic|novel}/{작품코드})는 서버가 HTML 로 그린다. 그 HTML 에서 읽는다.
성인 작품은 성인 인증된 로그인 세션 쿠키가 있어야 열린다 (없으면 m.mrblue.com/auth/login?reason=adult 로 이동).
세션은 `python cli.py login --platform mrblue` 로 사람이 브라우저에서 로그인해 sessions/ 에 저장한다.
"""
import html
import pickle
import re
import time

from .base_crawler import PASS_THROUGH, BaseCrawler, HttpUnavailable, SessionExpiredError
from config import MRBLUE_COOKIE_FILE

SITE = 'https://www.mrblue.com'
LOGIN_URL = SITE + '/login'
_WORK = re.compile(r'mrblue\.com/(webtoon|comic|novel)/([A-Za-z0-9_]+)')
# 세로 표지 480x694. detail_original.jpg 는 가로 배너(1600x520), thumb_sq.jpg 는 174x174 라 쓰지 않는다
COVER = 'https://img.mrblue.com/prod_img/comics/{pid}/cover_w480.jpg'
_TYPES = {'webtoon': '웹툰', 'comic': '웹툰', 'novel': '웹소설'}

_TITLE = re.compile(r'<p class="title">(.*?)</p>', re.S)
_GENRE = re.compile(r'<div class="info">\s*<span><a href="/(?:webtoon|comic|novel)/genre/[^"]*">([^<]+)</a></span>')
# 작가 영역: '<span>그림/글</span> <span class="authorname">…</span>' 이 역할마다 이어진다
#   (그림 · 글이 다르면 '<span class="width35">그림</span> …<i class="linebreak"></i><span class="width35">글</span> …')
_TXT_BLOCK = re.compile(r'<p class="title">.*?<div class="txt">\s*<p>(.*?)</p>', re.S)
_AUTHORS = re.compile(r'<span(?: class="[^"]*")?>([^<]+)</span>\s*<span class="authorname[^"]*">(.*?)</span>', re.S)
_AGE = re.compile(r'<span>(\d+세 이용가|전체\s*이용가)</span>')
_KEYWORD = re.compile(r'<a class="keyword" href="/keywords/[^"]*">#([^<]+)</a>')
_SYNOPSIS = re.compile(r'<div class="txt-box">(.*?)</div>', re.S)
# 판매 · 통계 · 형식 키워드는 해시태그로 쓰지 않는다
#   스크롤 · 오리지널 · 2만~3만원 · 50화이상 · 별점4점이상 · 리뷰100개+ · 완결 · 대여 · 19+
#   미블뿐 · 할인이벤트 · 선물함 · 기다리면무료 · 4주이내신작 · 후방주의
MRBLUE_META_TAGS = re.compile(
    r'^(?:스크롤|오리지널|독점|완결|연재중?|대여|소장|무료|단행본|미블뿐|할인이벤트|선물함|기다리면\s*무료|후방주의|\d+\s*[주일]\s*이내\s*신작|\d+\+|.*\d+\s*[만천]?\s*원.*|\d+\s*화\s*(?:이상|이하|미만)?'
    r'|(?:별점|평점|리뷰)\s*\d+.*)$')


def mrblue_work(url: str) -> tuple[str, str] | None:
    """작품 주소 → (구분, 작품코드)."""
    m = _WORK.search(url or '')
    return (m.group(1), m.group(2)) if m else None


def _text(fragment: str) -> str:
    t = re.sub(r'<br\s*/?>', '\n', fragment or '', flags=re.I)
    t = html.unescape(re.sub(r'<[^>]+>', '', t)).replace('\xa0', ' ')
    return '\n'.join(line.strip() for line in t.split('\n')).strip()


def parse_mrblue_page(src: str, url: str) -> dict | None:
    """작품 페이지 HTML → 수집 결과. 작품 정보가 없으면(로그인 화면 등) None."""
    work = mrblue_work(url)
    title = _TITLE.search(src or '')
    if not work or not title:
        return None
    section, pid = work

    roles = {'author': '', 'illustrator': '', 'original_author': ''}
    names = []
    block = _TXT_BLOCK.search(src)
    for role, names_html in _AUTHORS.findall(block.group(1) if block else ''):
        role = role.replace(' ', '')
        for name in (_text(n) for n in re.findall(r'<a[^>]*>(.*?)</a>', names_html, re.S) or [names_html]):
            if not name:
                continue
            if name not in names:
                names.append(name)
            if '원작' in role:
                roles['original_author'] = roles['original_author'] or name
            else:
                if '글' in role:
                    roles['author'] = roles['author'] or name
                if '그림' in role:
                    roles['illustrator'] = roles['illustrator'] or name

    age = _AGE.search(src)
    age_text = age.group(1) if age else ''
    if age_text.startswith('19'):
        age_text = '18세 이용가'
    elif age_text.startswith('전체'):
        age_text = '전체연령가'
    genre = _GENRE.search(src)
    synopsis = _SYNOPSIS.search(src)
    return {
        'platform': 'MRBLUE',
        'works_name': _text(title.group(1)),
        'artist_name': ', '.join(names),
        **roles,
        'age_classification': age_text,
        'description': _text(synopsis.group(1)) if synopsis else '',
        'genre': _text(genre.group(1)) if genre else '',
        'hashtags': list(dict.fromkeys(k for k in (html.unescape(x).strip() for x in _KEYWORD.findall(src))
                                       if k and not MRBLUE_META_TAGS.match(k))),
        'thumbnail_url': COVER.format(pid=pid),
        'works_type': _TYPES.get(section, ''),
        'source_url': f'{SITE}/{section}/{pid}',
    }


class MrblueCrawler(BaseCrawler):
    REPORT_PLATFORM = 'mrblue'
    LOGIN_URL_MARKERS = ('auth/login', '/login')
    REQUEST_INTERVAL = 1.0

    def _cookie_header(self) -> str:
        if not MRBLUE_COOKIE_FILE.exists():
            return ''
        try:
            cookies = pickle.load(open(MRBLUE_COOKIE_FILE, 'rb'))
        except Exception:
            return ''
        return '; '.join(f"{c['name']}={c['value']}" for c in cookies if 'mrblue' in (c.get('domain') or ''))

    def crawl_detail_http(self, url: str) -> dict | None:
        work = mrblue_work(url)
        if not work:
            raise HttpUnavailable('작품 주소 아님')
        cookie = self._cookie_header()
        if not cookie:
            raise HttpUnavailable('로그인 세션 없음')
        src = self.http_get(f'{SITE}/{work[0]}/{work[1]}', headers={'Cookie': cookie})
        if src is None:
            return None
        result = parse_mrblue_page(src, url)
        if result is None:
            # 로그인 화면으로 돌아왔거나(세션 만료 · 성인 인증 안 됨) 화면 구조가 바뀌었다
            raise HttpUnavailable('작품 정보 없음 (세션 만료 의심)')
        return result

    def crawl_detail(self, url: str) -> dict | None:
        work = mrblue_work(url)
        if not work:
            return None
        try:
            self.driver.get(f'{SITE}/{work[0]}/{work[1]}')
            time.sleep(1)
            if self.is_login_page():
                raise SessionExpiredError(f'미스터블루 로그인 화면으로 이동: {url}')
            return parse_mrblue_page(self.driver.page_source, url)
        except PASS_THROUGH:
            raise
        except Exception as e:
            self._log.warning('crawl_detail 실패 (%s): %s', url, e)
            return None

    # ---------------------------------------------------------------- 로그인

    def _http_adult_ok(self) -> bool:
        try:
            return self.crawl_detail_http(f'{SITE}/webtoon/wt_000070142') is not None
        except HttpUnavailable:
            return False

    def login_with_cookies(self) -> bool:
        if not MRBLUE_COOKIE_FILE.exists():
            return False
        try:
            self.driver.get(SITE)
            for c in pickle.load(open(MRBLUE_COOKIE_FILE, 'rb')):
                c.pop('expiry', None)
                try:
                    self.driver.add_cookie(c)
                except Exception:
                    pass
            self.driver.get(f'{SITE}/webtoon/wt_000070142')
            return not self.is_login_page() and parse_mrblue_page(self.driver.page_source, self.driver.current_url) is not None
        except Exception as e:
            print(f'⚠️ 미스터블루 쿠키 로그인 실패: {e}')
            return False

    def login(self) -> bool:
        if self._http_adult_ok():
            print('✅ [미스터블루] 저장된 세션으로 성인 작품 열림')
            return True
        if self.login_with_cookies():
            return True
        import os
        if os.environ.get('DOCKER_ENV') == 'true':
            return False
        print('\n' + '=' * 60)
        print('🔐 브라우저 창에서 미스터블루 로그인을 완료하세요 (성인 인증된 계정). 최대 5분')
        print('=' * 60 + '\n')
        self.driver.get(LOGIN_URL)
        deadline = time.time() + 300
        while time.time() < deadline:
            time.sleep(4)
            if self.is_login_page():
                continue
            pickle.dump(self.driver.get_cookies(), open(MRBLUE_COOKIE_FILE, 'wb'))
            if self._http_adult_ok():
                print('✅ [미스터블루] 로그인 · 성인 작품 확인. 쿠키를 저장했습니다.')
                return True
        print('⏰ 미스터블루 로그인 대기 시간 초과 (성인 인증이 안 된 계정이면 성인 작품이 열리지 않습니다).')
        return False

    def search_url_by_title(self, title: str) -> str | None:
        return None
