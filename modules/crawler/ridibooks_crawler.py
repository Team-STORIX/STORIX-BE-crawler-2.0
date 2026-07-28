import re
import time
import pickle

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, InvalidSessionIdException
from urllib3.exceptions import ReadTimeoutError as _DriverTimeoutError

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

    def search_url_by_title(self, title: str) -> str | None:
        """제목으로 리디북스 작품 URL 검색. 정확·부분·유사(≥임계값) 매칭 반환 (불일치 시 None).

        검색 결과 카드는 emotion 해시 클래스라 안정적인 셀렉터가 없어, `/books/<id>`
        링크(속성 기반)를 훑어 (책ID, 화면표시 제목) 쌍을 모아 매칭한다. 같은 책에
        썸네일·제목 두 앵커가 걸리므로 책ID로 합쳐 텍스트가 있는 쪽을 제목으로 쓴다.
        """
        import urllib.parse

        self.driver.get(f"https://ridibooks.com/search?q={urllib.parse.quote(title)}")
        try:
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/books/']"))
            )
        except TimeoutException:
            print(f"   ⚠️  '{title}' 검색 결과 로딩 실패 — 스킵")
            return None
        time.sleep(1)

        norm_title = self._norm_title(title)

        # 책ID → (href, 표시 제목). 검색 결과 순서(가장 위)가 먼저 들어오도록 유지.
        by_id: dict[str, tuple[str, str]] = {}
        for el in self.driver.find_elements(By.CSS_SELECTOR, "a[href*='/books/']"):
            href = el.get_attribute('href') or ''
            m = re.search(r'/books/(\d+)', href)
            if not m:
                continue
            book_id = m.group(1)
            clean_url = f"https://ridibooks.com/books/{book_id}"
            text = (el.get_attribute('title') or el.text or '').strip().split('\n')[0].strip()
            prev = by_id.get(book_id)
            # 텍스트가 있는 앵커(제목 링크)를 우선 채택, 없으면 URL만이라도 보존
            if prev is None or (not prev[1] and text):
                by_id[book_id] = (clean_url, text)

        exact = partial = fuzzy = None
        fuzzy_score = 0.0
        for clean_url, text in by_id.values():
            if not text:
                continue
            norm_text = self._norm_title(text)
            if norm_text == norm_title:
                exact = (clean_url, text)
                break
            if partial is None and (norm_title in norm_text or norm_text in norm_title):
                partial = (clean_url, text)
            ratio = self._title_ratio(norm_title, norm_text)
            if ratio >= self.TITLE_FUZZY_THRESHOLD and ratio > fuzzy_score:
                fuzzy_score = ratio
                fuzzy = (clean_url, text)

        if exact:
            print(f"   ↳ 검색 결과 (정확): {exact[0]} [{exact[1]}]")
            return exact[0]
        if partial:
            print(f"   ↳ 검색 결과 (부분): {partial[0]} [{partial[1]}]")
            return partial[0]
        if fuzzy:
            print(f"   ↳ 검색 결과 (유사 {fuzzy_score:.0%}): {fuzzy[0]} [{fuzzy[1]}]")
            return fuzzy[0]

        print(f"   ⚠️  '{title}'과 일치하는 검색 결과 없음 — 스킵")
        return None

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
                # 1612 등 실제 도서가 아닌 서비스 메타 페이지 제외
                _RIDI_NON_BOOK_IDS = {'1612'}
                if book_id.isdigit() and book_id not in _RIDI_NON_BOOK_IDS:
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

            # 더보기 클릭 (description 펼치기 — line-clamp 해제)
            try:
                more_btn = self.driver.find_element(
                    By.XPATH,
                    "//h2[normalize-space()='작품 소개']/following-sibling::div[1]//button"
                )
                self.driver.execute_script("arguments[0].click();", more_btn)
                time.sleep(0.5)
            except Exception:
                pass

            # 설명 - '작품 소개' h2 다음 형제 div innerText (\n 보존)
            # 구조: h2[작품 소개] → div.container → div.text-wrapper(버튼 제외) → div(br 포함 텍스트)
            desc = ""
            try:
                desc = self.driver.execute_script("""
                    const h2s = document.querySelectorAll('h2');
                    for (const h2 of h2s) {
                        if (h2.textContent.trim() === '작품 소개') {
                            const container = h2.nextElementSibling;
                            if (!container) continue;
                            const textDiv = container.querySelector('div');
                            if (textDiv && textDiv.innerText && textDiv.innerText.trim().length > 20) {
                                return textDiv.innerText.trim();
                            }
                            return container.innerText.trim() || null;
                        }
                    }
                    return null;
                """) or ""
                desc = desc.strip()
            except Exception:
                pass
            if not desc:
                for sel in [".book_intro", ".intro", ".synopsis", ".detail_introduce", ".book_detail_description", ".content_detail"]:
                    try:
                        _el = self.driver.find_element(By.CSS_SELECTOR, sel)
                        t = (self.driver.execute_script("return arguments[0].innerText", _el) or "").strip()
                        if t:
                            desc = t
                            break
                    except Exception:
                        pass
            if not desc:
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

            # 방법 1: #BookDetailHomeAuthorProfileTab 탭 버튼
            # 구조: <button>역할<div/>(구분자)이름</button> → button.text = "역할\n이름"
            try:
                tab_buttons = self.driver.find_elements(
                    By.CSS_SELECTOR, "#BookDetailHomeAuthorProfileTab ul button"
                )
                for btn in tab_buttons:
                    text = btn.text.strip()
                    if not text:
                        continue
                    parts = text.split('\n', 1)
                    if len(parts) < 2:
                        continue
                    role = parts[0].strip()
                    name = parts[1].strip()
                    if not name:
                        continue
                    if role in ("글", "지은이", "작가", "저자"):
                        if not author: author = name
                    elif role in ("그림", "그린이"):
                        if not illustrator: illustrator = name
                    elif role == "원작":
                        if not original_author: original_author = name
                    elif role == "글/그림":
                        if not author: author = name
                        if not illustrator: illustrator = name
                    else:
                        if not author: author = name
            except Exception:
                pass

            # 방법 2: 기존 셀렉터 fallback (구버전 레이아웃)
            if not author and not illustrator and not original_author:
                _AUTHOR_ROW_SELECTORS = [
                    ".book_info_author .author_role_wrap",
                    ".authors_detail .contributor",
                    ".author_info_wrap .author_info",
                    "[class*='authorInfo'] [class*='author']",
                    ".book_author li",
                    ".author li",
                ]
                for _sel in _AUTHOR_ROW_SELECTORS:
                    try:
                        contributor_rows = self.driver.find_elements(By.CSS_SELECTOR, _sel)
                        if not contributor_rows:
                            continue
                        for row in contributor_rows:
                            try:
                                role_el = None
                                for rs in [".role", ".author_role", ".type", "[class*='role']", "[class*='Role']"]:
                                    try:
                                        role_el = row.find_element(By.CSS_SELECTOR, rs)
                                        break
                                    except Exception:
                                        pass
                                role = role_el.text.strip().rstrip(":") if role_el else ""
                                name_el = None
                                for ns in [".name", "a", ".author_name", "[class*='name']", "[class*='Name']", "span"]:
                                    try:
                                        ne = row.find_element(By.CSS_SELECTOR, ns)
                                        if ne.text.strip():
                                            name_el = ne
                                            break
                                    except Exception:
                                        pass
                                name = name_el.text.strip() if name_el else row.text.strip()
                                if not name:
                                    continue
                                if "글" in role or "작가" in role or "저자" in role or "지은이" in role:
                                    if not author: author = name
                                elif "그림" in role or "그린이" in role:
                                    if not illustrator: illustrator = name
                                elif "원작" in role:
                                    if not original_author: original_author = name
                                else:
                                    if not author: author = name
                            except Exception:
                                pass
                        if author or illustrator or original_author:
                            break
                    except Exception:
                        pass

            # 방법 3: 메타 태그 폴백
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
                        elif "웹툰" in t or "만화" in t or "webtoon" in href.lower() or "comic" in href.lower():
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
                    elif "웹툰" in section or "만화" in section:
                        works_type = "웹툰"
                except Exception:
                    pass

            if not works_type:
                works_type = "웹툰"

            return {
                "platform": "RIDIBOOKS",
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

        except (InvalidSessionIdException, SessionExpiredError, _DriverTimeoutError):
            raise
        except Exception as e:
            self._log.error("crawl_detail 실패 (%s): %s", url, e)
            return None
