import time
import random
import pickle

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, InvalidSessionIdException
from urllib3.exceptions import ReadTimeoutError as _DriverTimeoutError

from .base_crawler import BaseCrawler, SessionExpiredError

from config import NAVER_COOKIE_FILE, NAVER_ID, NAVER_PW

class NaverCrawler(BaseCrawler):
    _platform = 'naver_webtoon'
    
    def _is_logged_in(self) -> bool:
        """NID_AUT 쿠키 존재 여부로 네이버 로그인 확인 (XPATH보다 신뢰성 높음)."""
        self.driver.get("https://www.naver.com")
        time.sleep(1)
        return any(c['name'] == 'NID_AUT' for c in self.driver.get_cookies())

    def _wait_for_manual_login(self, timeout_sec: int = 180) -> bool:
        """2단계 폴링: 1) nid.naver.com 이탈 감지 → 2) comic.naver.com에서 성인 인증 완료 대기."""
        print("\n" + "=" * 60)
        print("🔐 브라우저 창에서 직접 로그인 & 성인 인증을 완료하세요.")
        print(f"   로그인 완료 시 자동으로 감지됩니다 (최대 {timeout_sec // 60}분).")
        print("=" * 60 + "\n")

        # 1단계: 로그인 후 nid.naver.com 이탈 대기 (최대 timeout_sec)
        phase1_deadline = time.time() + timeout_sec
        while time.time() < phase1_deadline:
            time.sleep(3)
            try:
                if "nid.naver.com" not in self.driver.current_url:
                    break
            except Exception:
                continue
        else:
            print("⏰ 로그인 대기 시간 초과.")
            return False

        # 2단계: NID_AUT 쿠키로 로그인 확인 (내비게이션 없이 현재 페이지에서)
        print("   ↳ 로그인 감지됨. 로그인 상태를 확인합니다...")
        phase2_deadline = time.time() + 30
        while time.time() < phase2_deadline:
            time.sleep(3)
            try:
                if any(c['name'] == 'NID_AUT' for c in self.driver.get_cookies()):
                    return True
            except Exception:
                pass

        print("⏰ 로그인 상태 확인 실패 (NID_AUT 쿠키 없음).")
        return False

    def _js_set_input(self, element, value):
        """네이버 React 입력 필드에 값을 주입한다. send_keys는 봇 감지에 걸리므로 JS native setter를 사용."""
        self.driver.execute_script("""
            var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            setter.call(arguments[0], arguments[1]);
            arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
            arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
        """, element, value)

    def _login_with_credentials(self) -> bool:
        """NAVER_ID / NAVER_PW 환경변수로 자동 로그인을 시도한다."""
        if not NAVER_ID or not NAVER_PW:
            return False
        print("🔑 환경변수 자격증명으로 네이버 로그인을 시도합니다.")
        try:
            self.driver.get("https://nid.naver.com/nidlogin.login")
            time.sleep(2)

            wait = WebDriverWait(self.driver, 10)
            id_field = wait.until(EC.presence_of_element_located((By.ID, "id")))
            pw_field = self.driver.find_element(By.ID, "pw")

            self._js_set_input(id_field, NAVER_ID)
            time.sleep(random.uniform(0.3, 0.7))
            self._js_set_input(pw_field, NAVER_PW)
            time.sleep(random.uniform(0.3, 0.7))

            login_btn = self.driver.find_element(By.ID, "log.login")
            login_btn.click()
            time.sleep(3)

            if self._is_logged_in():
                print("✅ 자격증명 로그인 성공. 쿠키를 저장합니다.")
                pickle.dump(self.driver.get_cookies(), open(NAVER_COOKIE_FILE, "wb"))
                return True

            print("⚠️ 자격증명 로그인 실패 (CAPTCHA 또는 추가 인증 필요).")
            return False
        except Exception as e:
            print(f"⚠️ 자격증명 로그인 중 오류: {e}")
            return False

    def login(self):
        self.driver.get("https://comic.naver.com/index")
        time.sleep(1)

        if NAVER_COOKIE_FILE.exists():
            print(f"🍪 기존 쿠키 파일을 적용합니다.")
            try:
                cookies = pickle.load(open(NAVER_COOKIE_FILE, "rb"))
                for c in cookies:
                    if 'expiry' in c: del c['expiry']
                    c['domain'] = '.naver.com'
                    self.driver.add_cookie(c)
                if self._is_logged_in():
                    print("✅ 쿠키 로그인 성공")
                    return True
                print("⚠️ 쿠키 만료 또는 로그인 실패. 다음 수단을 시도합니다.")
            except Exception as e:
                print(f"⚠️ 쿠키 적용 실패: {e}")

        if self._login_with_credentials():
            return True

        import os
        if os.environ.get("DOCKER_ENV") == "true":
            raise RuntimeError(
                "Docker 환경에서는 수동 로그인 불가.\n"
                "NAVER_ID/NAVER_PW 환경변수를 설정하거나 sessions/naver_cookies.pkl을 생성하세요."
            )

        self.driver.get("https://nid.naver.com/nidlogin.login")
        time.sleep(2)
        if self._wait_for_manual_login():
            print("✅ 로그인 확인됨. 쿠키를 저장합니다.")
            pickle.dump(self.driver.get_cookies(), open(NAVER_COOKIE_FILE, "wb"))
            return True

        print("❌ 네이버 로그인 실패. 쿠키를 저장하지 않습니다.")
        return False

    def login_with_cookies(self):
        """워커 전용: 쿠키 파일로만 로그인. 수동 입력 없이 실패 시 False 반환."""
        if not NAVER_COOKIE_FILE.exists():
            return False
        try:
            self.driver.get("https://comic.naver.com/index")
            time.sleep(1)
            cookies = pickle.load(open(NAVER_COOKIE_FILE, "rb"))
            for c in cookies:
                if 'expiry' in c: del c['expiry']
                c['domain'] = '.naver.com'
                self.driver.add_cookie(c)
            return self._is_logged_in()
        except Exception as e:
            print(f"⚠️ 워커 쿠키 로그인 실패: {e}")
            return False

    def search_url_by_title(self, title: str) -> str | None:
        """제목으로 네이버 웹툰 URL 검색. 정확 매칭 또는 부분 매칭만 반환 (불일치 시 None)."""
        import urllib.parse
        import re

        self.driver.get(f"https://comic.naver.com/search?keyword={urllib.parse.quote(title)}")
        time.sleep(2)

        def normalize(s: str) -> str:
            return re.sub(r'[\s\[\]()·∙#]', '', s).lower()

        norm_title = normalize(title)

        # 검색 결과 섹션 내 링크 우선 탐색
        candidates = []
        for section_xpath in [
            "//section[contains(@class,'search')]//a[contains(@href,'list?titleId=')]",
            "//div[contains(@class,'SearchResult') or contains(@class,'search_result')]//a[contains(@href,'list?titleId=')]",
            "//ul[contains(@class,'list')]//a[contains(@href,'list?titleId=')]",
        ]:
            candidates = self.driver.find_elements(By.XPATH, section_xpath)
            if candidates:
                break

        if not candidates:
            candidates = self.driver.find_elements(
                By.XPATH, "//a[contains(@href,'list?titleId=') and not(contains(@href,'comment'))]"
            )

        exact = None
        partial = None

        for el in candidates:
            href = el.get_attribute('href') or ''
            if 'titleId=' not in href or 'comment' in href:
                continue
            text = (el.get_attribute('title') or el.text or '').replace('[독점]', '').replace('휴재', '').strip()
            if not text:
                continue
            norm_text = normalize(text)

            if norm_text == norm_title:
                exact = (href, text)
                break

            if partial is None and (norm_title in norm_text or norm_text in norm_title):
                partial = (href, text)

        if exact:
            print(f"   ↳ 검색 결과 (정확): {exact[0]} [{exact[1]}]")
            return exact[0]

        if partial:
            print(f"   ↳ 검색 결과 (부분): {partial[0]} [{partial[1]}]")
            return partial[0]

        print(f"   ⚠️  '{title}'과 일치하는 검색 결과 없음 — 스킵")
        return None

    # 무한 스크롤 로직
    def _scroll_down(self):
        self.driver.execute_script("window.scrollBy(0, Math.floor(window.innerHeight * 0.85));")

    def load_all_items(self, item_xpath, max_rounds=200, idle_rounds=3, wait_per_round=4.0):
        print("📜 무한 스크롤 로딩 시작...")
        def count_items():
            try: return len(self.driver.find_elements(By.XPATH, item_xpath))
            except Exception: return 0

        prev_count = count_items()
        stagnant = 0
        for _ in range(max_rounds):
            self._scroll_down()
            time.sleep(random.uniform(0.3, 0.6))
            
            grew = False
            start = time.time()
            while time.time() - start < wait_per_round:
                cur = count_items()
                if cur > prev_count:
                    prev_count = cur
                    grew = True
                    break
                time.sleep(0.2)

            if grew: stagnant = 0
            else:
                stagnant += 1
                if stagnant >= idle_rounds: break

        print(f"📜 로딩 완료. 총 {prev_count}개 항목 감지됨.")
        return prev_count

    def get_genre_urls(self, genre_url):
        print(f"📂 URL 수집 시작: {genre_url}")
        self.driver.get(genre_url)
        try:
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.XPATH, "//div[@id='content']/div[1]/ul"))
            )
            item_xpath = "//div[@id='content']/div[1]/ul/li/a"
            self.load_all_items(item_xpath)
            
            elems = self.driver.find_elements(By.XPATH, item_xpath)
            urls = list(set([e.get_attribute('href') for e in elems if 'titleId=' in e.get_attribute('href')]))
            print(f"✅ 총 {len(urls)}개 작품 URL 수집 완료")
            return urls
        except Exception as e:
            print(f"❌ 목록 수집 중 오류: {e}")
            return []

    def crawl_detail(self, url):
        try:
            self.driver.get(url)
            if "nid.naver.com" in self.driver.current_url:
                raise SessionExpiredError(f"네이버 로그인 리다이렉트 감지: {url}")

            wait = WebDriverWait(self.driver, 10)
            
            # 제목 추출 및 전처리
            title_raw = wait.until(EC.visibility_of_element_located((By.XPATH, '//*[@id="content"]/div[1]/div/h2'))).text
            title = title_raw.replace("휴재", "").replace(" [독점]", "").strip()

            artist = self.driver.find_element(By.XPATH, '//*[@id="content"]/div[1]/div/div[1]/span').text
            desc = self.driver.find_element(By.XPATH, '//*[@id="content"]/div[1]/div/div[2]/p').text
            genre = self.driver.find_element(By.XPATH, '//*[@id="content"]/div[1]/div/div[2]/div/div/a[1]').text.lstrip('#').strip()
            age = self.driver.find_element(By.XPATH, '//*[@id="content"]/div[1]/div/div[1]/em').text.strip().split('∙')[-1].strip()
            
            hashtags = []
            try:
                all_tags = self.driver.find_elements(By.XPATH, "//*[@id='content']/div[1]/div/div[2]/div/div/a")
                
                # 첫 번째(장르)를 제외한 나머지
                if len(all_tags) > 1:
                    hashtags = [t for tag in all_tags[1:] if (t := tag.text.strip().lstrip('#'))]
            except NoSuchElementException:
                pass 


            thumb = ""
            try: thumb = self.driver.find_element(By.CSS_SELECTOR, "meta[property='og:image']").get_attribute("content")
            except Exception: pass

            return {
                "platform": "네이버 웹툰", 
                "works_name": title, 
                "artist_name": artist,
                "age_classification": age, 
                "description": desc, 
                "genre": genre,
                "hashtags": hashtags, 
                "thumbnail_url": thumb, 
                "works_type": "웹툰", 
                "source_url": url
            }
        
        except (InvalidSessionIdException, _DriverTimeoutError):
            raise
        except Exception as e:
            self._log.warning("crawl_detail 실패 (%s): %s", url, e)
            return None