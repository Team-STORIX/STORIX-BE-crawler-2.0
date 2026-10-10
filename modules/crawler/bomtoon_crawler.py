"""봄툰 크롤러 (#59).

상세는 봄툰 화면이 쓰는 작품 API 로 받는다:
    GET https://www.bomtoon.com/api/balcony-api-v2/contents/{alias}   헤더 x-balcony-id: BOMTOON_COM
성인 작품은 성인 인증된 로그인 세션이 있어야 열린다 (없으면 ADULT_ONLY_CONTENTS).
세션은 `python cli.py login --platform bomtoon` 으로 사람이 브라우저에서 로그인해 sessions/ 에 저장한다.
"""
import json
import pickle
import re
import time

from .base_crawler import PASS_THROUGH, BaseCrawler, HttpUnavailable, SessionExpiredError
from config import BOMTOON_COOKIE_FILE

SITE = 'https://www.bomtoon.com'
CONTENTS_API = SITE + '/api/balcony-api-v2/contents/{}'
# 로그인 세션(next-auth). user.accessToken.token 을 API 에 Authorization: Bearer 로 붙인다 (쿠키만으로는 성인 작품이 안 열림)
SESSION_API = SITE + '/api/auth/session'
# 작품명 검색 (화면 검색과 같은 API)
SEARCH_API = SITE + '/api/balcony-api-v2/search/title'
API_HEADERS = {'Accept': 'application/json', 'x-balcony-id': 'BOMTOON_COM', 'x-balcony-timeZone': 'Asia/Seoul'}
_ALIAS = re.compile(r'bomtoon\.com/detail/([^/?#\s]+)')

# 첫 태그는 장르다. 연재 요일 · 봄툰 자체 분류 · 이벤트 태그는 작품 내용이 아니라 해시태그로 쓰지 않는다
#   토요연재 · 열흘연재 · 4일/14일/24일 · 2/12/22일 · 단편선 · 오직봄툰 · 기다봄 · 웹툰판 · 월드드랍 · 봄툰공모전_당선웹툰 · BL비엘
BOMTOON_META_TAGS = re.compile(
    r'^(?:.+연재|\d+(?:일)?(?:/\d+(?:일)?)*일|단편선|오직봄툰|기다봄|웹툰판|소설판|월드드랍|BL비엘|GL지엘|.*공모전.*|.*봄툰.*)$')
# 작가가 직접 다는 자유 태그(isExtra)는 '츤데레수' · '혐관' 같은 키워드도 있지만 문장('드실래요_잘생긴..감자♥') ·
# 캐릭터 커플링('도해x단조')이 섞여 있다. 짧은 단어형만 남긴다
_EXTRA_KEYWORD = re.compile(r'^[가-힣A-Za-z0-9/]{1,8}$')
_PAIRING = re.compile(r'[가-힣0-9][xX×][가-힣0-9]')
# CARTOON 은 봄툰의 만화(출판 만화) 분류다. 크롤러 작품 유형은 웹툰 · 웹소설 둘이라 웹툰으로 본다
_TYPES = {'COMIC': '웹툰', 'CARTOON': '웹툰', 'NOVEL': '웹소설'}


def _keep_tag(tag: dict) -> bool:
    name = (tag.get('name') or '').strip()
    if not name or BOMTOON_META_TAGS.match(name):
        return False
    if tag.get('isExtra'):
        return bool(_EXTRA_KEYWORD.match(name)) and not _PAIRING.search(name)
    return True


def bomtoon_alias(url: str) -> str | None:
    m = _ALIAS.search(url or '')
    return m.group(1).strip() if m else None


def parse_bomtoon_contents(data: dict, url: str) -> dict:
    """작품 API 의 data → 수집 결과."""
    tags = [t for t in data.get('tags') or [] if (t.get('name') or '').strip()]
    genre = tags[0]['name'].strip() if tags else ''
    hashtags = list(dict.fromkeys(t['name'].strip() for t in tags[1:] if _keep_tag(t)))
    # 작가 역할이 전부 AUTHOR 라 글 · 그림을 구분할 수 없다. 이름만 순서대로 낸다 (검수 단계 정규화가 나눈다)
    names = []
    for c in data.get('creators') or []:
        name = (c.get('name') or '').strip()
        if name and name not in names:
            names.append(name)
    thumbs = {t.get('type'): t.get('imagePath') for t in data.get('thumbnails') or []}
    return {
        'platform': 'BOMTOON',
        'works_name': (data.get('title') or '').strip(),
        'artist_name': ', '.join(names),
        # 성인 여부만 내려온다. 성인이 아니면 15세인지 전체인지 알 수 없어 비운다 (BE 는 빈 값을 덮어쓰지 않는다)
        'age_classification': '18세 이용가' if data.get('isAdult') else '',
        'description': (data.get('synopsis') or '').replace('\r\n', '\n').strip(),
        'genre': genre,
        'hashtags': hashtags,
        # 표지는 세로형 VERTICAL(315x415, 다른 플랫폼 표지와 같은 세로 비율). DETAIL 은 상세 화면 가로 배너(720x330),
        # ORIGINAL_TYPE_* 는 작품 속 장면 홍보 컷이라 쓰지 않는다. 세로형이 없으면 정사각 MAIN (2026-10-10 확인)
        'thumbnail_url': thumbs.get('VERTICAL') or thumbs.get('MAIN') or thumbs.get('SQUARE') or '',
        'works_type': _TYPES.get(data.get('type'), ''),
        'source_url': f"{SITE}/detail/{data.get('alias') or bomtoon_alias(url)}",
    }


def _api_result(body: str, url: str) -> dict | None:
    """API 응답 → data. 없는 작품이면 None, 성인 인증이 필요하면 SessionExpiredError."""
    try:
        res = json.loads(body)
    except ValueError:
        raise HttpUnavailable('JSON 아님')
    if res.get('result') == 'SUCCESS':
        return res.get('data')
    code = (res.get('error') or {}).get('code')
    if code == 'ADULT_ONLY_CONTENTS':
        raise SessionExpiredError(f'봄툰 성인 인증 세션 필요: {url}')
    if code in ('NOT_EXIST', 'NOT_FOUND_CONTENTS', 'CONTENTS_NOT_FOUND', 'NOT_FOUND'):
        return None
    raise HttpUnavailable(f'봄툰 API 오류 {code}')


class BomtoonCrawler(BaseCrawler):
    REPORT_PLATFORM = 'bomtoon'
    LOGIN_URL_MARKERS = ('/user/login',)
    REQUEST_INTERVAL = 1.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._token: tuple[str, float] | None = None  # (토큰, 만료 시각 epoch 초)

    def _access_token(self) -> str:
        """저장된 쿠키로 세션 토큰을 받는다. 만료 전까지 재사용. 로그인 세션이 없으면 ''."""
        if self._token and self._token[1] - 60 > time.time():
            return self._token[0]
        cookie = self._cookie_header()
        if not cookie:
            return ''
        try:
            body = self.http_get(SESSION_API, headers={'Accept': 'application/json', 'Cookie': cookie})
            token = ((json.loads(body or '{}').get('user') or {}).get('accessToken') or {})
        except (HttpUnavailable, ValueError):
            return ''
        if not token.get('token'):
            return ''
        self._token = (token['token'], (token.get('expiredAt') or 0) / 1000)
        return self._token[0]

    def _cookie_header(self) -> str:
        if not BOMTOON_COOKIE_FILE.exists():
            return ''
        try:
            cookies = pickle.load(open(BOMTOON_COOKIE_FILE, 'rb'))
        except Exception:
            return ''
        return '; '.join(f"{c['name']}={c['value']}" for c in cookies if 'bomtoon' in (c.get('domain') or ''))

    def crawl_detail_http(self, url: str) -> dict | None:
        alias = bomtoon_alias(url)
        if not alias:
            raise HttpUnavailable('작품 주소 아님')
        headers = dict(API_HEADERS)
        token = self._access_token()
        if token:
            headers['Authorization'] = f'Bearer {token}'
        body = self.http_get(CONTENTS_API.format(alias), headers=headers)
        if body is None:
            return None
        try:
            data = _api_result(body, url)
        except SessionExpiredError:
            # 저장된 쿠키로 안 열린다 — 브라우저 세션으로 다시 연다
            raise HttpUnavailable('성인 작품: 로그인 세션으로 다시 엶')
        return parse_bomtoon_contents(data, url) if data else None

    def crawl_detail(self, url: str) -> dict | None:
        """브라우저 세션(로그인 쿠키)으로 같은 API 를 부른다."""
        alias = bomtoon_alias(url)
        if not alias:
            return None
        try:
            self.driver.get(f'{SITE}/detail/{alias}')
            body = self._fetch_in_page(CONTENTS_API.format(alias))
            data = _api_result(body, url)
            return parse_bomtoon_contents(data, url) if data else None
        except PASS_THROUGH:
            raise
        except Exception as e:
            self._log.warning('crawl_detail 실패 (%s): %s', url, e)
            return None

    def _fetch_in_page(self, api_url: str) -> str:
        """브라우저 세션으로 API 를 부른다. 화면과 같이 세션 토큰을 Authorization 에 붙인다."""
        return self.driver.execute_async_script(
            """const [url, headers, sessionUrl, done] = arguments;
               fetch(sessionUrl, {credentials: 'include'}).then(r => r.json()).catch(() => ({}))
                 .then(s => {
                   const t = s && s.user && s.user.accessToken && s.user.accessToken.token;
                   const h = t ? {...headers, Authorization: 'Bearer ' + t} : headers;
                   return fetch(url, {headers: h, credentials: 'include'});
                 })
                 .then(r => r.text()).then(done).catch(e => done(String(e)));""",
            api_url, API_HEADERS, SESSION_API)

    # ---------------------------------------------------------------- 로그인

    def _adult_check(self) -> str:
        """성인 작품 API 결과 코드. SUCCESS 면 로그인 · 성인 인증이 된 것이다."""
        try:
            body = self._fetch_in_page(CONTENTS_API.format('URMYWORLD'))
            res = json.loads(body)
            return res.get('result') if res.get('result') == 'SUCCESS' else (res.get('error') or {}).get('code', '?')
        except Exception as e:
            return f'확인 실패: {e}'

    def _adult_session_ok(self) -> bool:
        return self._adult_check() == 'SUCCESS'

    def login_with_cookies(self) -> bool:
        if not BOMTOON_COOKIE_FILE.exists():
            return False
        try:
            self.driver.get(SITE)
            time.sleep(1)
            for c in pickle.load(open(BOMTOON_COOKIE_FILE, 'rb')):
                c.pop('expiry', None)
                try:
                    self.driver.add_cookie(c)
                except Exception:
                    pass
            self.driver.get(SITE)
            return self._adult_session_ok()
        except Exception as e:
            print(f'⚠️ 봄툰 쿠키 로그인 실패: {e}')
            return False

    def _http_adult_ok(self) -> bool:
        """저장된 쿠키(세션 토큰)로 성인 작품 API 가 열리는지. 상세는 HTTP 로 받으므로 이게 되면 브라우저 로그인은 필요 없다."""
        token = self._access_token()
        if not token:
            return False
        try:
            body = self.http_get(CONTENTS_API.format('URMYWORLD'),
                                 headers={**API_HEADERS, 'Authorization': f'Bearer {token}'})
            return json.loads(body or '{}').get('result') == 'SUCCESS'
        except (HttpUnavailable, ValueError):
            return False

    def login(self) -> bool:
        if self._http_adult_ok():
            print('✅ [봄툰] 저장된 세션으로 성인 작품 열림')
            return True
        if self.login_with_cookies():
            print('✅ [봄툰] 쿠키 로그인 성공 (성인 작품 열림)')
            return True
        import os
        if os.environ.get('DOCKER_ENV') == 'true':
            return False
        print('\n' + '=' * 60)
        print('🔐 브라우저 창에서 봄툰 로그인을 완료하세요 (성인 인증된 계정).')
        print('   성인 작품이 열리면 자동으로 감지합니다 (최대 5분).')
        print('=' * 60 + '\n')
        self.driver.get(SITE + '/user/login')
        deadline = time.time() + 300
        last = None
        while time.time() < deadline:
            time.sleep(4)
            if '/user/login' in (self.driver.current_url or ''):
                continue
            # 로그인 화면을 벗어나면 쿠키부터 저장한다. 성인 인증 확인은 따로 알린다
            pickle.dump(self.driver.get_cookies(), open(BOMTOON_COOKIE_FILE, 'wb'))
            code = self._adult_check()
            if code == 'SUCCESS':
                print('✅ [봄툰] 로그인 · 성인 인증 확인. 쿠키를 저장했습니다.')
                return True
            if code != last:
                print(f'⏳ [봄툰] 로그인 쿠키 저장. 성인 작품은 아직 안 열림: {code} '
                      '(성인 인증 · 성인 작품 보기 설정을 확인하세요)', flush=True)
                last = code
        print('⏰ 봄툰 로그인 대기 시간 초과.')
        return False

    def search_candidates(self, title: str) -> list[dict]:
        """봄툰 검색 API → 후보 (일치도 순). 성인 작품도 받으려면 로그인 세션 토큰이 필요하다."""
        import urllib.parse
        headers = dict(API_HEADERS)
        token = self._access_token()
        if token:
            headers['Authorization'] = f'Bearer {token}'
        body = self.http_get(SEARCH_API + '?' + urllib.parse.urlencode({
            'searchText': title, 'isIncludeAdult': 'true', 'page': 0, 'size': 30,
            'isCheckDevice': 'true', 'contentsThumbnailType': 'MAIN'}), headers=headers)
        try:
            contents = ((json.loads(body or '{}').get('data') or {}).get('contents')) or []
        except ValueError:
            return []
        raw = [(f"{SITE}/detail/{c['alias']}", c.get('title') or '', _TYPES.get(c.get('type')))
               for c in contents if c.get('alias')]
        return self.rank_candidates(title, raw)

    def search_url_by_title(self, title: str) -> str | None:
        candidates = self.search_candidates(title)
        return candidates[0]['url'] if candidates else None
