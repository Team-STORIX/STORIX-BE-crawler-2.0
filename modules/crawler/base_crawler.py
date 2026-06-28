import os
import time
import random

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import InvalidSessionIdException
from urllib3.exceptions import ReadTimeoutError as _DriverTimeoutError

from modules.logger import get_logger
from monitor.session_watcher import watcher


class SessionExpiredError(Exception):
    pass


class BaseCrawler:
    _platform: str = ''

    def __init__(self, headless: bool = False):
        self.driver = None
        self._headless = headless
        self._log = get_logger(self.__class__.__name__)

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
    
    def wait_for_login_gui(self, message: str):
        in_docker = os.environ.get("DOCKER_ENV") == "true"
        if in_docker:
            return
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            messagebox.showinfo("로그인 필요", message)
            root.destroy()
        except Exception:
            input(f"\n{message}\n👉 완료 후 [Enter]를 누르세요...")

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
                if self._platform:
                    watcher.record_null(self._platform)
                result = None

            if result is not None:
                if self._platform:
                    watcher.record_success(self._platform)
                return result
            if attempt < max_attempts:
                wait = 2.0 * attempt
                self._log.warning("재시도 %d/%d (%s), %.0f초 대기", attempt, max_attempts - 1, url, wait)
                time.sleep(wait)
        if self._platform:
            watcher.record_null(self._platform)
        self._log.error("최대 재시도 횟수 초과, 포기: %s", url)
        return None

    # 추상 메서드
    def login(self):
        raise NotImplementedError

    def crawl_detail(self, url):
        raise NotImplementedError