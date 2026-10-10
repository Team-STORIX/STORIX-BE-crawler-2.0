import os
import re
import time
import random
from difflib import SequenceMatcher

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import InvalidSessionIdException
from urllib3.exceptions import ReadTimeoutError as _DriverTimeoutError

from modules.logger import get_logger


class SessionExpiredError(Exception):
    pass


# 플랫폼이 제목 뒤에 붙이는 라벨. 제목 비교 전에 뗀다 ('넷카마 펀치!!! [완결]' → '넷카마 펀치!!!')
# 판본 표기(19세 완전판 · 개정판)는 떼지 않는다 — BE 가 다른 작품으로 보고, 본편과 따로 수집한다
_TITLE_LABELS = re.compile(r'\s*\[(?:완결|독점|단행본|휴재|연재)\]|^\s*\[e북\]\s*')


# 본편에 딸린 이야기. 같은 작품이라 따로 수집하지 않는다 — 검색어에 없으면 후보에서 뺀다.
# 외전이 19세여도 작품 연령은 본편 기준이다 (2026-10-10 사용자 결정)
SIDE_STORY_WORDS = ('외전', '특별편', '번외')
# 판본. BE 가 다른 작품으로 판정하므로 본편과 함께 있으면 둘 다 수집한다 (2026-10-10 사용자 결정)
EDITION_WORDS = ('완전판', '개정판')


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


class BaseCrawler:
    # 제목 검색 매칭 임계값. 완전일치·부분일치(포함)로 못 잡은 후보 중
    # 정규화 문자열 유사도가 이 값 이상이면 마지막 순위로 채택한다.
    TITLE_FUZZY_THRESHOLD: float = 0.9

    def __init__(self, headless: bool = False):
        self.driver = None
        self._headless = headless
        self._log = get_logger(self.__class__.__name__)

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
        q, t = cls._norm_title(strip_title_labels(query)), cls._norm_title(strip_title_labels(text))
        if not q or not t:
            return None
        if q == t:
            return 0, 1.0
        if t.startswith(q) or q.startswith(t):
            return 1, min(len(q), len(t)) / max(len(q), len(t))
        ratio = cls._title_ratio(q, t)
        if ratio >= cls.TITLE_FUZZY_THRESHOLD:
            return 2, ratio
        return None

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

        if headless:
            options.add_argument("--headless=new")

        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36')

        # Selenium 4.6+ 내장 드라이버 관리자 사용 (webdriver-manager 불필요)
        self.driver = webdriver.Chrome(options=options)
        self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """Object.defineProperty(navigator, 'webdriver', { get: () => undefined })"""
        })
    
    def close_driver(self):
        if self.driver:
            self.driver.quit()
            self.driver = None
    
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
            if not self.login_with_cookies():
                if in_docker:
                    self._log.error("세션 만료. Docker 환경에서는 수동 재로그인 불가.")
                    return
                self._log.warning("쿠키 만료 감지. 수동 재로그인 대기 중...")
                self.login()
        except Exception as e:
            self._log.error("드라이버 재시작 실패: %s", e)

    def crawl_detail_with_retry(self, url: str, max_attempts: int = 3):
        for attempt in range(1, max_attempts + 1):
            try:
                result = self.crawl_detail(url)
            except (InvalidSessionIdException, SessionExpiredError, _DriverTimeoutError) as e:
                self._log.error("드라이버 재시작 (%s): %s", url, e)
                self._restart_driver()
                result = None

            if result is not None:
                return result
            if attempt < max_attempts:
                wait = 2.0 * attempt
                self._log.warning("재시도 %d/%d (%s), %.0f초 대기", attempt, max_attempts - 1, url, wait)
                time.sleep(wait)
        self._log.error("최대 재시도 횟수 초과, 포기: %s", url)
        return None

    # 추상 메서드
    def login(self):
        raise NotImplementedError

    def crawl_detail(self, url):
        raise NotImplementedError