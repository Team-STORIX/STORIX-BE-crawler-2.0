import os
import time
import random
import urllib.error
import urllib.request

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import InvalidSessionIdException
from urllib3.exceptions import ReadTimeoutError as _DriverTimeoutError

from modules.logger import get_logger
from . import robots
from .robots import RobotsDisallowed
from .titles import SIDE_STORY_WORDS, norm_title, title_match, title_ratio


class SessionExpiredError(Exception):
    pass


class RateLimitedError(Exception):
    """403 · 429 차단 화면. 간격을 늘려 재시도하고, 계속되면 그 플랫폼을 중단한다 (#6)."""


class AuthExpiredError(Exception):
    """세션이 만료됐고 재로그인도 실패했다. 그 플랫폼을 중단한다 (#6)."""


class HttpUnavailable(Exception):
    """HTTP 로 상세를 받지 못했다 (응답 형식이 바뀜 · 로그인이 필요함 등). 브라우저로 다시 연다 (#7)."""


# crawl_detail 이 삼키지 말고 위로 올려야 하는 예외. 크롤러마다 except 로 다시 던진다
PASS_THROUGH = (InvalidSessionIdException, SessionExpiredError, _DriverTimeoutError, RateLimitedError, AuthExpiredError,
                RobotsDisallowed)

_BLOCK_MARKERS = ('403 Forbidden', '429 Too Many', 'Too Many Requests', 'Access Denied', '요청이 너무 많', '비정상적인 접근')

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'




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
    if works_type == '만화':
        extra = [c for c in candidates if c['type_hint'] == '단행본']  # 리디 만화 e북 카드는 [e북] 으로 나온다
    return same + unknown + extra


def manual_login_timeout(default: int) -> int:
    """수동 로그인을 기다리는 시간(초). 폰 원격 로그인(#77)은 LOGIN_WAIT_SEC 로 늘린다."""
    return int(os.getenv('LOGIN_WAIT_SEC') or default)


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
    TITLE_FUZZY_THRESHOLD: float = 0.9
    # 페이지 이동(driver.get) 사이 최소 간격(초). 리디는 연달아 열면 403 이 나서 넉넉히 둔다
    REQUEST_INTERVAL: float = 0.5
    MAX_INTERVAL: float = 30.0
    # 연속 차단이 이만큼 나면 그 플랫폼을 중단한다
    MAX_RATE_LIMITS: int = 3
    # 로그인 페이지 주소 표시. 목록 0건이 세션 만료 때문인지 가린다
    LOGIN_URL_MARKERS: tuple[str, ...] = ()
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

    # 제목 비교는 modules/crawler/titles 한 곳에서 한다 (#122)
    _norm_title = staticmethod(norm_title)
    _title_ratio = staticmethod(title_ratio)

    @classmethod
    def title_match(cls, query: str, text: str) -> tuple[int, float] | None:
        return title_match(query, text, cls.TITLE_FUZZY_THRESHOLD)

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
        """제목 검색 결과 → 후보 목록 (rank_candidates). 검색을 지원하는 크롤러만 구현한다 (platforms.searchable)."""
        return []

    def start_driver(self):
        if self.driver is not None: return
        in_docker = os.environ.get("DOCKER_ENV") == "true"
        headless = self._headless or in_docker
        label = "(headless)" if headless else ""
        print(f"🔧 브라우저를 시작합니다{label}...")
        options = Options()
        # 폰 원격 로그인(#77)은 세로 화면에 맞춘다
        options.add_argument(f"--window-size={os.getenv('CHROME_WINDOW_SIZE') or '1600,900'}")
        options.add_argument("--lang=ko-KR")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-extensions")
        # WebUSB 는 시작할 때 USB 장치를 훑으며 연다. 운영자 PC 에 연결된 USB 장치가 그동안 명령에 응답하지 못한다 (#102)
        options.add_argument("--disable-features=WebUSB")
        # 메모리 절감 (#7). 썸네일은 이미지 주소 문자열만 쓰므로 이미지를 받지 않는다.
        # DOM 이 만들어지면 바로 넘어간다 — 화면 요소는 크롤러마다 WebDriverWait 로 기다린다
        # 리눅스 Chrome 은 --lang 을 무시한다. 사이트가 보는 언어(Accept-Language)를 직접 정한다 — 영어면 네이버 캡차가 영어로 나온다
        prefs = {'intl.accept_languages': 'ko-KR,ko'}
        if self.block_images:
            prefs['profile.managed_default_content_settings.images'] = 2
        options.add_experimental_option('prefs', prefs)
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
        """driver.get 에 robots.txt 검사 · 플랫폼별 최소 간격 · 차단 화면 감지를 붙인다. 크롤러 코드는 그대로 driver.get 을 쓴다."""
        original = self.driver.get

        def get(url):
            robots.check(url, USER_AGENT)
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
        robots.check(url, USER_AGENT)
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

    def no_manual_login_on_server(self) -> None:
        """서버(Docker)는 로그인 화면을 못 띄운다 — 쿠키 로그인이 실패하면 세션 만료로 알린다 (#77).
        사람 로그인 화면을 열기 전에 크롤러마다 부른다."""
        if os.environ.get('DOCKER_ENV') != 'true':
            return
        from modules.crawler import platforms
        p = platforms.get(platforms.get(self.REPORT_PLATFORM).login)
        raise AuthExpiredError(f'{p.label} 쿠키 만료 — 서버에서는 로그인 화면을 못 띄웁니다. '
                               f'python cli.py login --platform {p.key} 로 쿠키를 새로 만드세요')

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

    def login(self):
        raise NotImplementedError

    def crawl_detail(self, url):
        raise NotImplementedError