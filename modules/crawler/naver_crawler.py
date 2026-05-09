import time
import random
import pickle

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException

from .base_crawler import BaseCrawler

from config import NAVER_COOKIE_FILE

class NaverCrawler(BaseCrawler):
    
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
                self.driver.get("https://comic.naver.com/index")
                time.sleep(2)
                
                if any(txt in self.driver.page_source for txt in ["관심웹툰", "MY", "로그아웃"]):
                    print("✅ 쿠키 로그인 성공")
                    return True
            except Exception as e:
                print(f"⚠️ 쿠키 적용 실패: {e}")

        import os
        if os.environ.get("DOCKER_ENV") == "true":
            raise RuntimeError(
                "Docker 환경에서는 수동 로그인 불가.\n"
                "로컬에서 먼저 로그인 후 sessions/naver_cookies.pkl을 생성하세요."
            )

        print("\n" + "="*40)
        print("🚨 [중요] 브라우저에서 수동 로그인을 진행해주세요!")
        print("1. 네이버 로그인 & 성인 인증까지 완료하세요.")
        print("2. 웹툰이 정상적으로 보이는 상태에서 아래 엔터를 누르세요.")
        print("="*40)

        self.driver.get("https://nid.naver.com/nidlogin.login")
        input("👉 준비가 다 되면 여기를 클릭하고 [Enter] 키를 누르세요...")
        
        time.sleep(2)
        try:
            handles = self.driver.window_handles
            if handles: self.driver.switch_to.window(handles[-1])
        except Exception: pass

        print("✅ 확인되었습니다. 현재 로그인 상태를 쿠키로 저장합니다.")
        pickle.dump(self.driver.get_cookies(), open(NAVER_COOKIE_FILE, "wb"))
        return True

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
            self.driver.get("https://comic.naver.com/index")
            time.sleep(2)
            return any(txt in self.driver.page_source for txt in ["관심웹툰", "MY", "로그아웃"])
        except Exception as e:
            print(f"⚠️ 워커 쿠키 로그인 실패: {e}")
            return False

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
                print(f"⚠️ [접근 불가] 로그인 풀림. SKIP")
                return None

            wait = WebDriverWait(self.driver, 10)
            
            # 제목 추출 및 전처리
            title_raw = wait.until(EC.visibility_of_element_located((By.XPATH, '//*[@id="content"]/div[1]/div/h2'))).text
            title = title_raw.replace("휴재", "").replace(" [독점]", "").strip()

            artist = self.driver.find_element(By.XPATH, '//*[@id="content"]/div[1]/div/div[1]/span').text
            desc = self.driver.find_element(By.XPATH, '//*[@id="content"]/div[1]/div/div[2]/p').text
            genre = self.driver.find_element(By.XPATH, '//*[@id="content"]/div[1]/div/div[2]/div/div/a[1]').text
            age = self.driver.find_element(By.XPATH, '//*[@id="content"]/div[1]/div/div[1]/em').text.strip().split('∙')[-1].strip()
            
            hashtags = []
            try:
                all_tags = self.driver.find_elements(By.XPATH, "//*[@id='content']/div[1]/div/div[2]/div/div/a")
                
                # 첫 번째(장르)를 제외한 나머지
                if len(all_tags) > 1:
                    hashtags = [tag.text.strip().lstrip('#') for tag in all_tags[1:]]
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
        
        except Exception as e:
            self._log.warning("crawl_detail 실패 (%s): %s", url, e)
            return None