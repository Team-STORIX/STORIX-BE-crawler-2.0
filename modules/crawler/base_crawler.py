import os
import re
import time
import random
import urllib.error
import urllib.request
from difflib import SequenceMatcher

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import InvalidSessionIdException
from urllib3.exceptions import ReadTimeoutError as _DriverTimeoutError

from modules.logger import get_logger


class SessionExpiredError(Exception):
    pass


class RateLimitedError(Exception):
    """403 · 429 차단 화면. 간격을 늘려 재시도하고, 계속되면 그 플랫폼을 중단한다 (#6)."""


class AuthExpiredError(Exception):
    """세션이 만료됐고 재로그인도 실패했다. 그 플랫폼을 중단한다 (#6)."""


class HttpUnavailable(Exception):
    """HTTP 로 상세를 받지 못했다 (응답 형식이 바뀜 · 로그인이 필요함 등). 브라우저로 다시 연다 (#7)."""


# crawl_detail 이 삼키지 말고 위로 올려야 하는 예외. 크롤러마다 except 로 다시 던진다
PASS_THROUGH = (InvalidSessionIdException, SessionExpiredError, _DriverTimeoutError, RateLimitedError, AuthExpiredError)

# 차단 화면의 제목 · 본문 표시
_BLOCK_MARKERS = ('403 Forbidden', '429 Too Many', 'Too Many Requests', 'Access Denied', '요청이 너무 많', '비정상적인 접근')

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'


# 플랫폼이 제목 뒤에 붙이는 라벨. 제목 비교 전에 뗀다 ('넷카마 펀치!!! [완결]' → '넷카마 펀치!!!')
# 판본 표기(19세 완전판 · 개정판)는 떼지 않는다 — BE 가 다른 작품으로 보고, 본편과 따로 수집한다
# 리디 e북은 개정판만 팔 때 제목 앞에 '개정판 | ' 을 붙인다 ('개정판 | 블랙 스완'). 판본 표기가 아니라 판매 표기다
_TITLE_LABELS = re.compile(r'\s*\[(?:완결|독점|단행본|휴재|연재)\]|^\s*\[e북\]\s*|^\s*개정판\s*\|\s*')


# 본편에 딸린 이야기. 같은 작품이라 따로 수집하지 않는다 — 검색어에 없으면 후보에서 뺀다.
# 외전이 19세여도 작품 연령은 본편 기준이다 (2026-10-10 사용자 결정)
SIDE_STORY_WORDS = ('외전', '특별편', '번외')
# 판본. BE 가 다른 작품으로 판정하므로 본편과 함께 있으면 둘 다 수집한다 (2026-10-10 사용자 결정)
EDITION_WORDS = ('완전판', '개정판')


# 앞부분 일치에서 뒤에 이어져도 되는 부제 · 판본 표기의 시작 글자
SUBTITLE_SEPARATORS = '-–—:~(（[<〈《'


def is_edition(text: str) -> bool:
    return any(w in (text or '') for w in EDITION_WORDS)


def strip_title_labels(title: str) -> str:
    return _TITLE_LABELS.sub('', title or '').strip()


def pick_candidates(candidates: list[dict], works_type: str | None) -> list[dict]:
    """원하는 유형(웹툰 · 웹소설 · 단행본 · None)에 맞는 후보를 시도할 순서대로.

    - 검색 화면의 유형 힌트가 원하는 유형과 같은 후보 → 힌트가 없는 후보 순. 다른 유형 힌트는 뺀다
    - 웹소설을 원하면 단행본 힌트는 웹소설 후보 다음에 둔다 (같은 작품의 e북판)
    - 원하는 유형이 없으면(None) 일치도 순 그대로
    """
    if not works_type:
        return list(candidates)
    same = [c for c in candidates if c['type_hint'] == works_type]
    unknown = [c for c in candidates if c['type_hint'] is None]
    extra = [c for c in candidates if works_type == '웹소설' and c['type_hint'] == '단행본']
    if works_type == '단행본':
        extra = [c for c in candidates if c['type_hint'] in ('웹소설', '웹툰')]
    return same + unknown + extra


def _check_empty_list(fn):
    import functools

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        result = fn(self, *args, **kwargs)
        if not result:
            self.report_empty_list(str(args[0]) if args else fn.__name__)
        return result

    wrapper._checks_empty = True
    return wrapper


class BaseCrawler:
    # 제목 검색 매칭 임계값. 완전일치·부분일치(포함)로 못 잡은 후보 중
    # 정규화 문자열 유사도가 이 값 이상이면 마지막 순위로 채택한다.
    TITLE_FUZZY_THRESHOLD: float = 0.9
    # 페이지 이동(driver.get) 사이 최소 간격(초). 리디는 연달아 열면 403 이 나서 넉넉히 둔다
    REQUEST_INTERVAL: float = 0.5
    MAX_INTERVAL: float = 30.0
    # 연속 차단이 이만큼 나면 그 플랫폼을 중단한다
    MAX_RATE_LIMITS: int = 3
    # 로그인 페이지 주소 표시. 목록 0건이 세션 만료 때문인지 가린다
    LOGIN_URL_MARKERS: tuple[str, ...] = ()
    # 수집 리포트에 쓰는 플랫폼 이름 (crawler/report.py)
    REPORT_PLATFORM: str = ''
    # HTTP 403 을 차단으로 볼지. 아니면 브라우저로 다시 연다 (로그인이 필요한 작품일 수 있다)
    HTTP_403_IS_BLOCK: bool = False
    # 브라우저로 상세를 이만큼 열 때마다 드라이버를 새로 띄운다. 긴 런에서 크롬 메모리가 쌓이지 않게 (#7)
    RESTART_EVERY: int = 100

    # 목록 수집 함수. 결과가 0건이면 수집 리포트에 남긴다 (__init_subclass__)
    _LIST_METHODS = ('get_genre_urls', 'get_list_urls', 'get_category_urls')

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        for name in cls._LIST_METHODS:
            fn = cls.__dict__.get(name)
            if fn is not None and not getattr(fn, '_checks_empty', False):
                setattr(cls, name, _check_empty_list(fn))

    def report_empty_list(self, section: str) -> None:
        """목록 0건 → 로그인 화면이면 AUTH_EXPIRED, 아니면 PARSING_FAILED (셀렉터가 깨졌거나 구조가 바뀜)."""
        from crawler import report
        platform = self.REPORT_PLATFORM or self.__class__.__name__
        if self.is_login_page():
            report.record(platform, report.AUTH_EXPIRED, section, '목록 수집 중 로그인 화면으로 이동')
            print(f'❌ [{platform}] 목록 0건 — 로그인 화면으로 튕김 (세션 만료): {section}')
        else:
            report.record(platform, report.PARSING_FAILED, section, '목록 0건')
            print(f'❌ [{platform}] 목록 0건 — 셀렉터 · 페이지 구조 확인 필요: {section}')

    def __init__(self, headless: bool = False):
        self.driver = None
        self._headless = headless
        self._log = get_logger(self.__class__.__name__)
        self._interval = self.REQUEST_INTERVAL
        self._last_get = 0.0
        self._rate_limits = 0
        self._browser_details = 0
        # 상세를 HTTP 로 먼저 받는다 (crawl_detail_http 가 있는 크롤러만). False 면 브라우저만 쓴다
        self.use_http = True
        # 이미지를 받지 않는다. 사람이 로그인 화면(보안문자)을 봐야 할 때만 끈다 (cli login)
        self.block_images = True

    @staticmethod
    def _norm_title(s: str) -> str:
        """제목 비교용 정규화: 공백·괄호·구분기호(·∙#) 제거 후 소문자화."""
        return re.sub(r'[\s\[\]()·∙#]', '', s).lower()

    @staticmethod
    def _title_ratio(norm_a: str, norm_b: str) -> float:
        """정규화된 두 제목의 유사도(0.0~1.0). SequenceMatcher 기반."""
        if not norm_a or not norm_b:
            return 0.0
        return SequenceMatcher(None, norm_a, norm_b).ratio()

    @classmethod
    def title_match(cls, query: str, text: str) -> tuple[int, float] | None:
        """검색어와 결과 제목의 일치 정도. (0 정확 | 1 앞부분 | 2 유사, 유사도), 불일치면 None.

        부분 일치는 한쪽이 다른 쪽의 앞부분일 때만 인정한다. 부제 · 라벨이 뒤에 붙는 경우
        ('레지나레나' → '레지나레나 - 용서받지 못한 그대에게')는 잡고, 앞에 다른 말이 붙은
        다른 작품('헌터는 조용히 살고 싶다' → '은퇴한 C급 헌터는 조용히 살고 싶다')은 거른다.
        """
        q_raw, t_raw = strip_title_labels(query), strip_title_labels(text)
        q, t = cls._norm_title(q_raw), cls._norm_title(t_raw)
        if not q or not t:
            return None
        if q == t:
            return 0, 1.0
        if t.startswith(q) or q.startswith(t):
            # 뒤에 붙은 부분이 부제 · 판본 표기일 때만 같은 작품 계열로 본다.
            # '상수리나무 아래 4컷 만화' 처럼 다른 말이 이어지면 다른 작품이다
            short, long_ = (q_raw, t_raw) if len(q) <= len(t) else (t_raw, q_raw)
            rest = cls._rest_after_prefix(short, long_).lstrip()
            if rest and rest[0] not in SUBTITLE_SEPARATORS:
                return None
            return 1, min(len(q), len(t)) / max(len(q), len(t))
        ratio = cls._title_ratio(q, t)
        if ratio >= cls.TITLE_FUZZY_THRESHOLD:
            return 2, ratio
        return None

    @classmethod
    def _rest_after_prefix(cls, short: str, long_: str) -> str:
        """long_ 에서 short 와 정규화 기준으로 겹치는 앞부분을 뺀 나머지 (원문 그대로)."""
        want = cls._norm_title(short)
        i = 0
        for pos, ch in enumerate(long_):
            n = cls._norm_title(ch)
            if not n:
                continue
            if i >= len(want):
                return long_[pos:]
            if want[i:i + len(n)] != n:
                return long_[pos:]
            i += len(n)
            if i >= len(want):
                return long_[pos + 1:]
        return ''

    @classmethod
    def rank_candidates(cls, query: str, raw: list[tuple[str, str, str | None]]) -> list[dict]:
        """검색 결과 (url, 표시 제목, 유형 힌트) → 일치하는 후보만 일치도 순으로.
        유형 힌트는 검색 화면에서 보이는 '웹툰' · '웹소설' · '단행본' (모르면 None)."""
        seen, out = set(), []
        # 외전 · 번외는 검색어에 없으면 후보에서 뺀다 (본편과 같은 작품, 연령은 본편 기준)
        skip = [w for w in SIDE_STORY_WORDS if w not in (query or '')]
        for order, (url, text, type_hint) in enumerate(raw):
            if not url or url in seen or any(w in (text or '') for w in skip):
                continue
            m = cls.title_match(query, text)
            if m is None:
                continue
            seen.add(url)
            out.append({'url': url, 'text': text, 'type_hint': type_hint,
                        'kind': ('정확', '부분', f'유사 {m[1]:.0%}')[m[0]], '_key': (m[0], -m[1], order)})
        out.sort(key=lambda c: c['_key'])
        for c in out:
            del c['_key']
        return out

    def search_candidates(self, title: str) -> list[dict]:
        """플랫폼별로 검색 결과를 후보 목록으로 돌려준다. 구현이 없으면 기존 단일 결과 검색을 쓴다."""
        url = self.search_url_by_title(title)
        return [{'url': url, 'text': title, 'type_hint': None, 'kind': ''}] if url else []

    def start_driver(self):
        if self.driver is not None: return
        in_docker = os.environ.get("DOCKER_ENV") == "true"
        headless = self._headless or in_docker
        label = "(headless)" if headless else ""
        print(f"🔧 브라우저를 시작합니다{label}...")
        options = Options()
        options.add_argument("--window-size=1600,900")
        options.add_argument("--lang=ko-KR")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-extensions")
        # 메모리 절감 (#7). 썸네일은 이미지 주소 문자열만 쓰므로 이미지를 받지 않는다.
        # DOM 이 만들어지면 바로 넘어간다 — 화면 요소는 크롤러마다 WebDriverWait 로 기다린다
        if self.block_images:
            options.add_experimental_option('prefs', {'profile.managed_default_content_settings.images': 2})
        options.page_load_strategy = 'eager'

        if headless:
            options.add_argument("--headless=new")

        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_argument(f'user-agent={USER_AGENT}')

        # Selenium 4.6+ 내장 드라이버 관리자 사용 (webdriver-manager 불필요)
        self.driver = webdriver.Chrome(options=options)
        self._wrap_get()
        self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """Object.defineProperty(navigator, 'webdriver', { get: () => undefined })"""
        })
    
    def close_driver(self):
        if self.driver:
            self.driver.quit()
            self.driver = None
    
    def _wrap_get(self) -> None:
        """driver.get 에 플랫폼별 최소 간격과 차단 화면 감지를 붙인다. 크롤러 코드는 그대로 driver.get 을 쓴다."""
        original = self.driver.get

        def get(url):
            self._throttle()
            try:
                return original(url)
            finally:
                self._last_get = time.time()
                self._check_blocked(url)

        self.driver.get = get

    def _throttle(self) -> None:
        wait = self._interval - (time.time() - self._last_get)
        if wait > 0:
            time.sleep(wait)

    def http_get(self, url: str, headers: dict | None = None, timeout: float = 15.0) -> str | None:
        """브라우저 없이 GET. 요청 간격은 driver.get 과 같이 지킨다.
        404 → None (없는 작품), 429(· 차단으로 보는 403) → RateLimitedError, 그 밖의 실패 → HttpUnavailable."""
        self._throttle()
        req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept-Language': 'ko-KR,ko;q=0.9',
                                                   **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 429 or (e.code == 403 and self.HTTP_403_IS_BLOCK):
                raise RateLimitedError(f'HTTP {e.code}: {url}')
            raise HttpUnavailable(f'HTTP {e.code}')
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise HttpUnavailable(str(e))
        finally:
            self._last_get = time.time()

    def _check_blocked(self, url: str) -> None:
        try:
            body = self.driver.execute_script("return document.body ? document.body.innerText.slice(0, 300) : ''")
            head = f'{self.driver.title} {body or ""}'
        except Exception:
            return
        if any(m in head for m in _BLOCK_MARKERS):
            raise RateLimitedError(f'차단 화면: {url}')

    def is_login_page(self) -> bool:
        try:
            return any(m in self.driver.current_url for m in self.LOGIN_URL_MARKERS)
        except Exception:
            return False

    def human_pause(self, min_s=1.0, max_s=2.0):
        time.sleep(random.uniform(min_s, max_s))
    
    def _restart_driver(self):
        in_docker = os.environ.get("DOCKER_ENV") == "true"
        try:
            self.close_driver()
        except Exception:
            pass
        try:
            self.start_driver()
            if self.login_with_cookies():
                return
            if in_docker:
                raise AuthExpiredError('세션 만료 — Docker 환경에서는 수동 재로그인 불가')
            self._log.warning("쿠키 만료 감지. 수동 재로그인 대기 중...")
            if not self.login():
                raise AuthExpiredError('세션 만료 — 재로그인 실패')
        except AuthExpiredError:
            raise
        except Exception as e:
            self._log.error("드라이버 재시작 실패: %s", e)

    def _has_http_detail(self) -> bool:
        return self.use_http and type(self).crawl_detail_http is not BaseCrawler.crawl_detail_http

    def _browser_detail(self, url: str):
        if self.RESTART_EVERY and self._browser_details >= self.RESTART_EVERY:
            self._log.info("상세 %d건마다 드라이버 재시작 (메모리 정리)", self.RESTART_EVERY)
            self._restart_driver()
            self._browser_details = 0
        self._browser_details += 1
        return self.crawl_detail(url)

    def crawl_detail_with_retry(self, url: str, max_attempts: int = 3):
        """상세 수집. HTTP 수집기가 있으면 먼저 쓰고, 못 받으면 브라우저로 연다. 결과 상태를 수집 리포트에 남긴다.
        차단이 MAX_RATE_LIMITS 번 연달아 나거나 재로그인이 실패하면 예외를 올려 그 플랫폼을 중단시킨다."""
        from crawler import report
        platform = self.REPORT_PLATFORM or self.__class__.__name__
        use_http = self._has_http_detail()
        attempt = 0
        while attempt < max_attempts:
            attempt += 1
            try:
                if use_http:
                    try:
                        result = self.crawl_detail_http(url)
                    except HttpUnavailable as e:
                        self._log.info("HTTP 로 못 받아 브라우저로 엶 (%s): %s", url, e)
                        use_http = False
                        attempt -= 1
                        continue
                    if result is None:
                        # HTTP 가 확실히 답했다 (내려간 작품 · 수집 대상 아님). 다시 열어도 같다
                        report.record(platform, report.DETAIL_NOT_FOUND, url)
                        return None
                else:
                    result = self._browser_detail(url)
            except AuthExpiredError as e:
                report.record(platform, report.AUTH_EXPIRED, url, str(e))
                raise
            except RateLimitedError as e:
                self._rate_limits += 1
                self._interval = min(self.MAX_INTERVAL, max(self._interval, 1.0) * 2)
                if self._rate_limits >= self.MAX_RATE_LIMITS:
                    report.record(platform, report.RATE_LIMITED, url, str(e))
                    raise
                self._log.warning("차단 감지 %d/%d — 간격 %.0f초로 늘려 재시도 (%s)",
                                  self._rate_limits, self.MAX_RATE_LIMITS, self._interval, url)
                time.sleep(self._interval)
                attempt -= 1  # 차단은 재시도 횟수에 넣지 않는다
                continue
            except (InvalidSessionIdException, SessionExpiredError, _DriverTimeoutError) as e:
                self._log.error("드라이버 재시작 (%s): %s", url, e)
                try:
                    self._restart_driver()
                except AuthExpiredError as auth:
                    report.record(platform, report.AUTH_EXPIRED, url, str(auth))
                    raise
                result = None

            if result is not None:
                self._rate_limits = 0
                report.record(platform, report.SUCCESS)
                return result
            if attempt < max_attempts:
                wait = 2.0 * attempt
                self._log.warning("재시도 %d/%d (%s), %.0f초 대기", attempt, max_attempts - 1, url, wait)
                time.sleep(wait)
        self._log.error("최대 재시도 횟수 초과, 포기: %s", url)
        report.record(platform, report.DETAIL_NOT_FOUND, url)
        return None

    def crawl_detail_http(self, url: str) -> dict | None:
        """브라우저 없이 상세를 받는다 (#7). dict = 수집 결과, None = 없는 작품 · 수집 대상 아님,
        HttpUnavailable = 브라우저로 다시 열어야 함. 구현한 크롤러만 쓴다 (_has_http_detail)."""
        raise HttpUnavailable('HTTP 수집기 없음')

    # 추상 메서드
    def login(self):
        raise NotImplementedError

    def crawl_detail(self, url):
        raise NotImplementedError