import time
import pickle

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, InvalidSessionIdException

from .base_crawler import BaseCrawler, SessionExpiredError
from config import RIDIBOOKS_COOKIE_FILE, RIDIBOOKS_LOGIN_URL, RIDIBOOKS_ID, RIDIBOOKS_PW


class RidibooksCrawler(BaseCrawler):
    _platform = 'ridibooks'

    def _save_cookies(self):
        try:
            pickle.dump(self.driver.get_cookies(), open(RIDIBOOKS_COOKIE_FILE, "wb"))
        except Exception as e:
            print(f"⚠️ 리디북스 쿠키 저장 실패: {e}")

    def login_with_cookies(self) -> bool:
        if not RIDIBOOKS_COOKIE_FILE.exists():
            return False
        try:
            self.driver.get("https://ridibooks.com")
            time.sleep(2)
            cookies = pickle.load(open(RIDIBOOKS_COOKIE_FILE, "rb"))
            for c in cookies:
                if 'expiry' in c:
                    del c['expiry']
                try:
                    self.driver.add_cookie(c)
                except Exception:
                    pass
            self.driver.get("https://ridibooks.com/account/myridi")
            time.sleep(3)
            if "account/login" in self.driver.current_url:
                return False
            return "myridi" in self.driver.current_url or "로그아웃" in self.driver.page_source
        except Exception as e:
            print(f"⚠️ 리디북스 쿠키 로그인 실패: {e}")
            return False

    def _login_with_credentials(self) -> bool:
        if not RIDIBOOKS_ID or not RIDIBOOKS_PW:
            return False
        print("🔑 환경변수 자격증명으로 리디북스 로그인을 시도합니다.")
        try:
            self.driver.get(RIDIBOOKS_LOGIN_URL)
            time.sleep(3)

            wait = WebDriverWait(self.driver, 10)
            id_field = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='email']")))
            id_field.clear()
            id_field.send_keys(RIDIBOOKS_ID)
            time.sleep(0.5)

            pw_field = self.driver.find_element(By.CSS_SELECTOR, "input[name='password']")
            pw_field.clear()
            pw_field.send_keys(RIDIBOOKS_PW)
            time.sleep(0.5)

            submit_btn = self.driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
            submit_btn.click()
            time.sleep(4)

            if "account/login" not in self.driver.current_url:
                print("✅ [리디북스] 자격증명 로그인 성공. 쿠키를 저장합니다.")
                self._save_cookies()
                return True

            print("⚠️ [리디북스] 자격증명 로그인 실패.")
            return False
        except Exception as e:
            print(f"⚠️ [리디북스] 자격증명 로그인 중 오류: {e}")
            return False

    def _wait_for_manual_login(self, timeout_sec: int = 180) -> bool:
        print("\n" + "=" * 60)
        print("🔐 브라우저 창에서 직접 리디북스 로그인을 완료하세요.")
        print(f"   로그인 완료 시 자동으로 감지됩니다 (최대 {timeout_sec // 60}분).")
        print("=" * 60 + "\n")

        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            time.sleep(3)
            try:
                cur = self.driver.current_url
                if "account/login" not in cur:
                    if "로그아웃" in self.driver.page_source or "myridi" in cur:
                        return True
            except Exception:
                pass

        print("⏰ 리디북스 로그인 대기 시간 초과.")
        return False

    def login(self) -> bool:
        if RIDIBOOKS_COOKIE_FILE.exists():
            print("🍪 [리디북스] 기존 쿠키 파일을 적용합니다.")
            if self.login_with_cookies():
                print("✅ [리디북스] 쿠키 로그인 성공")
                return True
            print("⚠️ [리디북스] 쿠키 로그인 실패. 다음 수단을 시도합니다.")

        if self._login_with_credentials():
            return True

        import os
        if os.environ.get("DOCKER_ENV") == "true":
            raise RuntimeError(
                "Docker 환경에서는 수동 로그인 불가.\n"
                "RIDIBOOKS_ID/RIDIBOOKS_PW 환경변수를 설정하거나 sessions/ridibooks_cookies.pkl을 생성하세요."
            )

        self.driver.get(RIDIBOOKS_LOGIN_URL)
        time.sleep(3)
        if self._wait_for_manual_login():
            print("✅ [리디북스] 로그인 확인됨. 쿠키를 저장합니다.")
            self._save_cookies()
            return True

        print("❌ [리디북스] 로그인 실패.")
        return False

    def get_category_urls(self, base_url: str, extra_params: str, max_count: int) -> list[str]:
        """카테고리 베스트셀러 URL 목록 수집 (스크롤 + 더보기 버튼)."""
        url = f"{base_url}?{extra_params}" if extra_params else base_url
        print(f"  📂 카테고리 URL 수집: {url}")
        self.driver.get(url)
        time.sleep(3)

        collected: set[str] = set()
        prev_count = 0
        stuck = 0

        while len(collected) < max_count:
            elems = self.driver.find_elements(By.CSS_SELECTOR, "a[href*='/books/']")
            for e in elems:
                href = e.get_attribute('href') or ''
                if '/books/' not in href:
                    continue
                book_id = href.rstrip('/').split('/books/')[-1].split('?')[0]
                if book_id.isdigit():
                    collected.add(f"https://ridibooks.com/books/{book_id}")

            curr_count = len(collected)
            if curr_count >= max_count:
                break

            if curr_count > prev_count:
                print(f"    └─ {curr_count}개 수집 중...")
                prev_count = curr_count
                stuck = 0
            else:
                stuck += 1
                if stuck >= 3:
                    break

            try:
                more_btn = self.driver.find_element(
                    By.XPATH,
                    "//button[contains(text(), '더보기') or contains(text(), '더 보기') or contains(@class, 'more')]"
                )
                self.driver.execute_script("arguments[0].scrollIntoView(true);", more_btn)
                time.sleep(0.3)
                self.driver.execute_script("arguments[0].click();", more_btn)
                time.sleep(2)
            except Exception:
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2)

        result = list(collected)[:max_count]
        print(f"  ✅ {len(result)}개 URL 수집 완료")
        return result

    def crawl_detail(self, url: str) -> dict | None:
        try:
            self.driver.get(url)
            wait = WebDriverWait(self.driver, 15)

            if "account/login" in self.driver.current_url:
                raise SessionExpiredError(f"리디북스 로그인 리다이렉트: {url}")

            try:
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "h1, h2, .title")))
            except TimeoutException:
                self._log.warning("페이지 로딩 시간 초과: %s", url)
                return None

            time.sleep(1)
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(0.5)
            self.driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(0.3)

            # 제목
            title = ""
            for sel in [
                "h1.book_title", "h2.book_title", ".book_info h1", ".book_info h2",
                "h1.title", ".title h1", "h1",
            ]:
                try:
                    el = self.driver.find_element(By.CSS_SELECTOR, sel)
                    title = el.text.strip()
                    if title:
                        break
                except Exception:
                    pass

            if not title:
                try:
                    title = self.driver.find_element(
                        By.CSS_SELECTOR, "meta[property='og:title']"
                    ).get_attribute("content") or ""
                except Exception:
                    pass

            if not title:
                return None

            # 썸네일
            thumb = ""
            for sel in [".thumbnail img", ".cover_image img", ".book_cover img", "img.cover"]:
                try:
                    thumb = self.driver.find_element(By.CSS_SELECTOR, sel).get_attribute("src") or ""
                    if thumb:
                        break
                except Exception:
                    pass
            if not thumb:
                try:
                    thumb = self.driver.find_element(
                        By.CSS_SELECTOR, "meta[property='og:image']"
                    ).get_attribute("content") or ""
                except Exception:
                    pass

            # 설명
            desc = ""
            try:
                desc = self.driver.find_element(
                    By.CSS_SELECTOR, "meta[property='og:description']"
                ).get_attribute("content") or ""
            except Exception:
                pass

            # 작가 정보
            author = ""
            illustrator = ""
            original_author = ""

            try:
                # 리디북스 작가 블록은 역할(글/그림/원작)별로 구분됨
                contributor_rows = self.driver.find_elements(
                    By.CSS_SELECTOR,
                    ".book_info_author .author_role_wrap, .authors_detail .contributor"
                )
                for row in contributor_rows:
                    try:
                        role = row.find_element(
                            By.CSS_SELECTOR, ".role, .author_role, .type"
                        ).text.strip().rstrip(":")
                        name = row.find_element(
                            By.CSS_SELECTOR, ".name, a, .author_name"
                        ).text.strip()
                        if not name:
                            continue
                        if "글" in role or "작가" in role or "저자" in role:
                            if not author:
                                author = name
                        elif "그림" in role:
                            if not illustrator:
                                illustrator = name
                        elif "원작" in role:
                            if not original_author:
                                original_author = name
                        else:
                            if not author:
                                author = name
                    except Exception:
                        pass
            except Exception:
                pass

            # 폴백: 메타 태그
            if not author and not illustrator:
                try:
                    meta_author = self.driver.find_element(
                        By.CSS_SELECTOR, "meta[name='author']"
                    ).get_attribute("content") or ""
                    author = meta_author
                except Exception:
                    pass

            parts = [n for n in [author, illustrator, original_author] if n]
            artist_name = ", ".join(dict.fromkeys(parts))

            # 연령 등급 (meta age rating이 가장 정확)
            age = ""
            try:
                age_meta = self.driver.find_element(
                    By.CSS_SELECTOR, "meta[property='books:rating:value'], meta[name='rating']"
                ).get_attribute("content") or ""
                if age_meta:
                    if "19" in age_meta or "adult" in age_meta.lower():
                        age = "18세 이용가"
                    else:
                        age = "전체연령가"
            except Exception:
                pass

            if not age:
                src = self.driver.page_source
                if "19세 이용가" in src or "성인 전용" in src or "청소년 이용불가" in src:
                    age = "18세 이용가"
                elif "15세 이용가" in src:
                    age = "15세 이용가"
                elif "12세 이용가" in src:
                    age = "12세 이용가"
                else:
                    age = "전체연령가"

            # works_type 감지: 카테고리/breadcrumb 링크 → og:section → fallback
            works_type = ""
            try:
                for sel in [
                    ".book_info_category a", ".category_tag a",
                    ".book_metadata a", "a[href*='category']", "nav a",
                ]:
                    els = self.driver.find_elements(By.CSS_SELECTOR, sel)
                    for el in els:
                        t = el.text.strip()
                        href = el.get_attribute("href") or ""
                        if "소설" in t or "novel" in href.lower():
                            works_type = "웹소설"
                            break
                        elif "만화" in t or "comic" in href.lower():
                            works_type = "만화"
                            break
                        elif "웹툰" in t or "webtoon" in href.lower():
                            works_type = "웹툰"
                            break
                    if works_type:
                        break
            except Exception:
                pass

            if not works_type:
                try:
                    section = self.driver.find_element(
                        By.CSS_SELECTOR, "meta[property='og:section'], meta[name='section']"
                    ).get_attribute("content") or ""
                    if "소설" in section:
                        works_type = "웹소설"
                    elif "만화" in section:
                        works_type = "만화"
                    elif "웹툰" in section:
                        works_type = "웹툰"
                except Exception:
                    pass

            if not works_type:
                works_type = "웹툰"

            return {
                "platform": "리디북스",
                "works_name": title,
                "artist_name": artist_name,
                "author": author,
                "illustrator": illustrator,
                "original_author": original_author,
                "age_classification": age,
                "description": desc,
                "genre": "",
                "hashtags": [],
                "thumbnail_url": thumb,
                "works_type": works_type,
                "source_url": url,
            }

        except (InvalidSessionIdException, SessionExpiredError):
            raise
        except Exception as e:
            self._log.error("crawl_detail 실패 (%s): %s", url, e)
            return None
