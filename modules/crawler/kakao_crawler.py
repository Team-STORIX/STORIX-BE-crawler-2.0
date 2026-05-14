import time
import random
import pickle
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, InvalidSessionIdException

from .base_crawler import BaseCrawler, SessionExpiredError
from config import KAKAO_COOKIE_FILE, KAKAO_LOGIN_URL, KAKAO_ID, KAKAO_PW

class KakaoCrawler(BaseCrawler):
    _platform = 'kakao_page'

    def _save_cookies(self):
        try:
            pickle.dump(self.driver.get_cookies(), open(KAKAO_COOKIE_FILE, "wb"))
        except Exception as e:
            print(f"⚠️ 카카오 쿠키 저장 실패: {e}")

    def login_with_cookies(self):
        """워커 전용: 쿠키 파일로만 로그인. 수동 입력 없이 실패 시 False 반환."""
        if not KAKAO_COOKIE_FILE.exists():
            return False
        try:
            self.driver.get("https://page.kakao.com")
            time.sleep(2)
            cookies = pickle.load(open(KAKAO_COOKIE_FILE, "rb"))
            for c in cookies:
                if 'expiry' in c: del c['expiry']
                try: self.driver.add_cookie(c)
                except Exception: pass
            self.driver.get("https://page.kakao.com/main")
            time.sleep(3)
            if "accounts.kakao.com" in self.driver.current_url or "kauth.kakao.com" in self.driver.current_url:
                return False
            return any(txt in self.driver.page_source for txt in ["로그아웃", "마이페이지", "내 서재"])
        except Exception as e:
            print(f"⚠️ 워커 카카오 쿠키 로그인 실패: {e}")
            return False

    def _wait_for_manual_login(self, timeout_sec: int = 180) -> bool:
        """2단계 폴링: 1) kakao auth 이탈 감지 → 2) page.kakao.com에서 로그인 확인."""
        print("\n" + "=" * 60)
        print("🔐 브라우저 창에서 직접 카카오 로그인 & 성인 인증을 완료하세요.")
        print(f"   로그인 완료 시 자동으로 감지됩니다 (최대 {timeout_sec // 60}분).")
        print("=" * 60 + "\n")
        deadline = time.time() + timeout_sec

        # 1단계: kakao auth 이탈 대기
        while time.time() < deadline:
            time.sleep(3)
            try:
                cur = self.driver.current_url
                if "accounts.kakao.com" not in cur and "kauth.kakao.com" not in cur:
                    break
            except Exception:
                continue
        else:
            print("⏰ 로그인 대기 시간 초과.")
            return False

        # 2단계: 현재 페이지(page.kakao.com)에서 내비게이션 없이 반복 확인
        inner_deadline = time.time() + min(120, max(0, deadline - time.time()))
        while time.time() < inner_deadline:
            time.sleep(4)
            try:
                cur = self.driver.current_url
                if "accounts.kakao.com" in cur or "kauth.kakao.com" in cur:
                    continue  # 성인 인증 중
                if any(txt in self.driver.page_source for txt in ["로그아웃", "마이페이지", "내 서재"]):
                    return True
            except Exception:
                pass

        print("⏰ 성인 인증 대기 시간 초과.")
        return False

    def _login_with_credentials(self) -> bool:
        """KAKAO_ID / KAKAO_PW 환경변수로 자동 로그인을 시도한다."""
        if not KAKAO_ID or not KAKAO_PW:
            return False
        print("🔑 환경변수 자격증명으로 카카오 로그인을 시도합니다.")
        try:
            self.driver.get(KAKAO_LOGIN_URL)
            time.sleep(3)

            wait = WebDriverWait(self.driver, 10)
            id_field = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='loginKey']")))
            id_field.click()
            id_field.send_keys(KAKAO_ID)
            time.sleep(random.uniform(0.3, 0.7))

            pw_field = self.driver.find_element(By.CSS_SELECTOR, "input[name='password']")
            pw_field.click()
            pw_field.send_keys(KAKAO_PW)
            time.sleep(random.uniform(0.3, 0.7))

            submit_btn = self.driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
            submit_btn.click()
            time.sleep(4)

            self.driver.get("https://page.kakao.com/main")
            time.sleep(3)

            if "accounts.kakao.com" in self.driver.current_url or "kauth.kakao.com" in self.driver.current_url:
                print("⚠️ [카카오] 자격증명 로그인 실패 (CAPTCHA 또는 추가 인증 필요).")
                return False

            if any(txt in self.driver.page_source for txt in ["로그아웃", "마이페이지", "내 서재"]):
                print("✅ [카카오] 자격증명 로그인 성공. 쿠키를 저장합니다.")
                self._save_cookies()
                return True

            print("⚠️ [카카오] 로그인 상태 확인 실패.")
            return False
        except Exception as e:
            print(f"⚠️ [카카오] 자격증명 로그인 중 오류: {e}")
            return False

    def login(self):
        # 쿠키 파일이 있으면 자동 로그인 먼저 시도
        if KAKAO_COOKIE_FILE.exists():
            print("🍪 [카카오] 기존 쿠키 파일을 적용합니다.")
            if self.login_with_cookies():
                print("✅ [카카오] 쿠키 로그인 성공")
                return True
            print("⚠️ [카카오] 쿠키 로그인 실패. 다음 수단을 시도합니다.")

        if self._login_with_credentials():
            return True

        import os
        if os.environ.get("DOCKER_ENV") == "true":
            raise RuntimeError(
                "Docker 환경에서는 수동 로그인 불가.\n"
                "KAKAO_ID/KAKAO_PW 환경변수를 설정하거나 sessions/kakao_cookies.pkl을 생성하세요."
            )

        self.driver.get(KAKAO_LOGIN_URL)
        time.sleep(3)
        if self._wait_for_manual_login():
            print("✅ [카카오] 로그인 확인됨. 쿠키를 저장합니다.")
            self._save_cookies()
            return True

        print("❌ [카카오] 로그인 실패. 쿠키를 저장하지 않습니다.")
        return False

    def search_url_by_title(self, title: str) -> str | None:
        """제목으로 카카오페이지 작품 URL 검색. 정확 매칭 또는 부분 매칭만 반환 (불일치 시 None)."""
        import urllib.parse
        import re

        self.driver.get(
            f"https://page.kakao.com/search/result?keyword={urllib.parse.quote(title)}&tab=content"
        )
        time.sleep(3)

        def normalize(s: str) -> str:
            return re.sub(r'[\s\[\]()·∙#]', '', s).lower()

        norm_title = normalize(title)

        candidates = self.driver.find_elements(By.XPATH, "//a[contains(@href,'/content/')]")

        exact = None
        partial = None

        for el in candidates:
            href = el.get_attribute('href') or ''
            if '/content/' not in href:
                continue
            text = (el.get_attribute('title') or el.text or '').strip()
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

    # 무한 스크롤 (개수 기반 + Wiggle)
    def load_all_items(self, item_xpath):
        print("📜 [카카오] 무한 스크롤 로딩 시작...")
        
        prev_count = len(self.driver.find_elements(By.XPATH, item_xpath))
        stuck_count = 0

        while True:
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1.5)

            elems = self.driver.find_elements(By.XPATH, item_xpath)
            curr_count = len(elems)
            
            if curr_count > prev_count:
                print(f"  └─ 로딩 중... ({prev_count} -> {curr_count}개)")
                prev_count = curr_count
                stuck_count = 0
            else:
                stuck_count += 1
                if stuck_count >= 3: 
                    print(f"✅ 로딩 완료. 총 {curr_count}개 항목 발견.")
                    break
                
                # Wiggle
                self.driver.execute_script("window.scrollBy(0, -500);")
                time.sleep(0.5)
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1.5)

    def get_list_urls(self, genre_url):
        print(f"📂 URL 수집 시작: {genre_url}")
        self.driver.get(genre_url)
        
        try:
            # 리스트 컨테이너 대기 
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.XPATH, "//div[contains(@class, 'grid')]"))
            )
            
            # 작품 링크 XPath
            item_xpath = "//a[contains(@href, '/content/')]"
            
            self.load_all_items(item_xpath)
            
            # URL 추출
            elems = self.driver.find_elements(By.XPATH, item_xpath)
            urls = []
            for e in elems:
                href = e.get_attribute('href')
                if href and '/content/' in href:
                    urls.append(href)
            
            urls = list(set(urls))
            print(f"✅ 총 {len(urls)}개 작품 URL 수집 완료")
            return urls
            
        except TimeoutException:
            print("❌ 리스트 로딩 실패")
            return []

    def crawl_detail(self, url):
        try:
            # 작품 정보 탭 이동
            target_url = url
            if "tab_type=about" not in url:
                target_url += "&tab_type=about" if "?" in url else "?tab_type=about"
            
            self.driver.get(target_url)
            wait = WebDriverWait(self.driver, 15)

            current = self.driver.current_url
            if "accounts.kakao.com" in current or "kauth.kakao.com" in current:
                raise SessionExpiredError(f"카카오 로그인 리다이렉트 감지: {url}")

            if "history/ticket" in self.driver.current_url:
                print(f"⚠️ [복구] 티켓 페이지로 잘못 진입함. 다시 이동: {target_url}")
                self.driver.get(target_url)
                time.sleep(2)

            title_xpath = '//*[@id="__next"]/div/div[2]/div[1]/div/div[1]/div[1]/div/div[2]/a/div/span[1]'
            try:
                wait.until(EC.presence_of_element_located((By.XPATH, title_xpath)))
            except Exception:
                self._log.warning("페이지 로딩 시간 초과: %s", url)
                return None

            # 하단 정보 로딩
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1.0)
            self.driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(0.5)


            # 제목
            try:
                title = self.driver.find_element(By.XPATH, title_xpath).text.strip()
                title = title.replace("휴재", "").replace("[독점]", "").strip()
            except Exception: title = ""

            # 설명
            try:
                desc = self.driver.find_element(By.CSS_SELECTOR, "meta[property='og:description']").get_attribute("content")
            except Exception: desc = ""

            # 상세 정보 리스트
            author, illustrator, original_author, age, genre, works_type = "", "", "", "", "", ""
            
            try:
                info_rows = self.driver.find_elements(By.XPATH, "//div[contains(@class, 'font-small1')]")
                
                for row in info_rows:
                    try:
                        spans = row.find_elements(By.XPATH, "./span | ./div") 
                        if len(spans) < 2: continue
                        
                        label = spans[0].text.strip()
                        value = spans[1].text.strip().replace("\n", " ")
                        
                        if label == "글": author = value
                        elif label == "그림": illustrator = value
                        elif label == "원작": original_author = value
                        elif label == "지은이": author = value
                        elif label == "글/그림": author = value; illustrator = value
                        elif label == "연령등급" or label == "이용등급":
                            if "19" in value or "청불" in value: age = "18세 이용가"
                            elif "15" in value: age = "15세 이용가"
                            elif "12" in value: age = "12세 이용가"
                            elif "전체" in value: age = "전체연령가"
                            else: age = ""
                        elif label == "분류":
                            if "소설" in value: works_type = "소설"
                            elif "웹툰" in value: works_type = "웹툰"
                            clean_genre = value.replace("웹소설", "").replace("소설", "").replace("웹툰", "").strip()
                            if clean_genre: genre = clean_genre
                    except Exception: continue
            except Exception: pass

            if genre == "무협": genre = "무협/사극"

            # artist_name 조합
            artist_map = {} 
            if author: artist_map.setdefault(author, set()).add("글")
            if illustrator: artist_map.setdefault(illustrator, set()).add("그림")
            if original_author: artist_map.setdefault(original_author, set()).add("원작")
            
            artist_list = []
            for name, roles in artist_map.items():
                sorted_roles = sorted(list(roles), key=lambda x: {"글":0, "그림":1, "원작":2}.get(x, 99))
                role_str = "/".join(sorted_roles)
                artist_list.append(f"{name} ∙ {role_str}")
            
            artist_name = " / ".join(artist_list)

            # 썸네일
            thumb = ""
            try:
                thumb = self.driver.find_element(By.XPATH, "//img[@alt='썸네일']").get_attribute("src")
            except Exception: pass

            # 해시태그
            hashtags = []
            try:
                wait.until(EC.presence_of_element_located((By.XPATH, "//a[contains(@href, 'themekeyword')]")))
                tag_elems = self.driver.find_elements(By.XPATH, "//a[contains(@href, '/search/themekeyword')]")
                for tag in tag_elems:
                    txt = tag.text.strip().replace("#", "")
                    if txt and txt not in hashtags:
                        hashtags.append(txt)
            except Exception: pass

            return {
                "platform": "카카오페이지",
                "works_name": title, 
                "artist_name": artist_name,
                "author": author,
                "illustrator": illustrator,
                "original_author": original_author,
                "age_classification": age, 
                "description": desc, 
                "genre": genre,
                "hashtags": hashtags, 
                "thumbnail_url": thumb, 
                "works_type": works_type, 
                "source_url": url
            }
        
        except InvalidSessionIdException:
            raise
        except Exception as e:
            self._log.error("crawl_detail 실패 (%s): %s", url, e)
            return None