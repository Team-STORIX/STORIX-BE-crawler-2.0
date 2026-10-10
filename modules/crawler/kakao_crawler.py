import re
import time
import random
import pickle
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, InvalidSessionIdException
from urllib3.exceptions import ReadTimeoutError as _DriverTimeoutError

from .base_crawler import PASS_THROUGH, BaseCrawler, SessionExpiredError, pick_candidates
from config import KAKAO_COOKIE_FILE, KAKAO_LOGIN_URL, KAKAO_ID, KAKAO_PW

class KakaoCrawler(BaseCrawler):
    REPORT_PLATFORM = 'kakao_page'
    LOGIN_URL_MARKERS = ('accounts.kakao.com', 'kauth.kakao.com')
    REQUEST_INTERVAL = 1.0


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

    def search_candidates(self, title: str) -> list[dict]:
        """카카오페이지 검색 결과 → 후보 목록 (일치도 순, 카드에 보이는 유형 힌트 포함)."""
        import urllib.parse

        self.driver.get(
            f"https://page.kakao.com/search/result?keyword={urllib.parse.quote(title)}&tab=content"
        )
        time.sleep(3)
        raw = []
        for el in self.driver.find_elements(By.XPATH, "//a[contains(@href,'/content/')]"):
            href = el.get_attribute('href') or ''
            if '/content/' not in href:
                continue
            name, type_hint = parse_kakao_search_card(el.get_attribute('title') or el.text or '')
            if name:
                raw.append((href, name, type_hint))
        return self.rank_candidates(title, raw)

    def search_url_by_title(self, title: str, works_type: str | None = None) -> str | None:
        """제목으로 카카오페이지 작품 URL 검색. 원하는 유형이 있으면 그 유형 후보를 먼저 고른다."""
        picked = pick_candidates(self.search_candidates(title), works_type)
        if not picked:
            print(f"   ⚠️  '{title}'과 일치하는 검색 결과 없음 — 스킵")
            return None
        c = picked[0]
        print(f"   ↳ 검색 결과 ({c['kind']}): {c['url']} [{c['text']}]")
        return c['url']

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

    def _title_sources(self, timeout: float = 5.0) -> tuple[str, str, str]:
        """(og:title, 문서 제목, 화면 큰 제목). 화면이 그려지기 전엔 og:title 이 '카카오페이지' 라 잠깐 기다린다."""
        js = """
            const og = document.querySelector("meta[property='og:title']");
            const big = document.querySelector('span[class*="font-large3-bold"]');
            return [og ? og.content : '', document.title || '', big ? big.innerText : ''];
        """
        end = time.time() + timeout
        sources = ('', '', '')
        while True:
            try:
                sources = tuple((v or '').strip() for v in self.driver.execute_script(js))
            except Exception:
                pass
            if parse_kakao_title(*sources) or time.time() > end:
                return sources
            time.sleep(0.5)

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

            # 페이지 로딩 대기: React 앱 루트만 확인
            try:
                wait.until(EC.presence_of_element_located((By.ID, "__next")))
            except Exception:
                self._log.warning("페이지 로딩 시간 초과: %s", url)
                return None

            # 하단 정보 로딩
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1.0)
            self.driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(0.5)

            # 설명 더보기 클릭 (접혀 있을 때 DOM에 텍스트가 없는 경우 대비)
            try:
                more_btn = self.driver.find_element(
                    By.XPATH,
                    "//div[@data-t-obj and contains(@data-t-obj, '더보기')]"
                )
                self.driver.execute_script("arguments[0].click();", more_btn)
                time.sleep(0.5)
            except Exception:
                pass

            # 제목. h1 · h2 는 "줄거리" · "키워드" 같은 섹션 제목이라 쓰지 않는다 (2026-10 화면 개편 후 전부 "줄거리"로 수집됨)
            title = parse_kakao_title(*self._title_sources())

            # 설명 - JS innerText로 \n 보존 (og:description은 개행 제거됨)
            # whitespace-pre-wrap span이 실제 설명 컨테이너 (class*="pre-wrap" 으로 매칭)
            desc = ""
            try:
                desc = self.driver.execute_script("""
                    // 정보 탭의 '줄거리' 섹션 본문. 섹션 상자 텍스트는 "줄거리\\n본문" 이라 제목은 파이썬에서 뗀다
                    const h = [...document.querySelectorAll('h2')].find(e => e.innerText.trim() === '줄거리');
                    for (let el = h && h.parentElement, i = 0; el && i < 5; el = el.parentElement, i++) {
                        const t = el.innerText.trim();
                        if (t.length > '줄거리'.length + 20) return t;
                    }
                    const candidates = [
                        document.querySelector('[class*="pre-wrap"]'),
                        document.querySelector('[class*="pre-line"]'),
                        document.querySelector('[class*="preLine"]'),
                        document.querySelector('[class*="pre_line"]'),
                        document.querySelector('pre'),
                        document.querySelector('[class*="desc"]'),
                        document.querySelector('[class*="Desc"]'),
                        document.querySelector('[class*="synopsis"]'),
                        document.querySelector('[class*="introduce"]'),
                    ];
                    for (const el of candidates) {
                        if (el && el.innerText && el.innerText.trim().length > 20) {
                            return el.innerText.trim();
                        }
                    }
                    return null;
                """) or ""
                desc = desc.strip()
            except Exception:
                desc = ""

            if not desc:
                try:
                    desc = self.driver.find_element(
                        By.CSS_SELECTOR, "meta[property='og:description']"
                    ).get_attribute("content") or ""
                except Exception:
                    desc = ""
            desc = clean_kakao_synopsis(desc)

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
                        elif label in ("지은이", "작가", "저자"): author = value
                        elif label == "글/그림": author = value; illustrator = value
                        elif label == "연령등급" or label == "이용등급":
                            if "19" in value or "청불" in value: age = "18세 이용가"
                            elif "15" in value: age = "15세 이용가"
                            elif "12" in value: age = "12세 이용가"
                            elif "전체" in value: age = "전체연령가"
                            else: age = ""
                        elif label == "분류":
                            if "웹소설" in value or "소설" in value: works_type = "웹소설"
                            elif "만화" in value and "웹툰" not in value: works_type = "만화"  # 출판 만화 (#64)
                            else: works_type = "웹툰"
                            clean_genre = value.replace("웹소설", "").replace("소설", "").replace("웹툰", "").replace("만화", "").strip()
                            if clean_genre: genre = clean_genre
                    except Exception: continue
            except Exception: pass

            if genre == "무협": genre = "무협"

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
                "platform": "KAKAO_PAGE",
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
        
        except PASS_THROUGH:
            raise
        except Exception as e:
            self._log.error("crawl_detail 실패 (%s): %s", url, e)
            return None


_KAKAO_SITE_NAMES = {'카카오페이지', '콘텐츠홈', ''}  # 화면이 그려지기 전 기본값
# [19세 완전판] 은 떼지 않는다. 떼면 BE 가 본편과 같은 작품으로 보고 본편을 19세판 정보로 덮어쓴다
_KAKAO_TITLE_LABELS = re.compile(r'\s*\[(완결|독점|휴재)\]')
# 정보 탭 섹션 제목. 작품명 자리에 이게 들어오면 잘못 읽은 것이다
KAKAO_SECTION_TITLES = {'줄거리', '키워드', '상세정보', '동일작', '이 작가의 다른 작품'}


def parse_kakao_title(og_title: str, doc_title: str, big_title: str) -> str:
    """카카오 작품명. og:title → 문서 제목('작품명 - 웹소설 | 카카오페이지') → 화면 큰 제목 순.
    사이트 이름 · 섹션 제목은 작품명이 아니므로 건너뛴다. 못 찾으면 ''."""
    doc = re.sub(r'\s*\|\s*카카오페이지\s*$', '', doc_title or '')
    doc = re.sub(r'\s+-\s+(웹툰|웹소설|책)\s*$', '', doc)
    for raw in (og_title, doc, big_title):
        t = _KAKAO_TITLE_LABELS.sub('', (raw or '').strip()).replace('휴재', '').strip()
        if t and t not in _KAKAO_SITE_NAMES and t not in KAKAO_SECTION_TITLES:
            return t
    return ''


def clean_kakao_synopsis(text: str) -> str:
    """줄거리 본문만 남긴다. 섹션 제목 '줄거리' 와 펼치기 버튼 글자를 뗀다."""
    t = (text or '').strip()
    t = re.sub(r'^줄거리\s*', '', t)
    t = re.sub(r'\s*(더보기|접기)$', '', t)
    return t.strip()


def parse_kakao_search_card(text: str) -> tuple[str, str | None]:
    """검색 결과 카드 → (작품명, 유형 힌트).

    카드 텍스트 첫 줄은 '작품, 넷카마 펀치!!! [완결], 15세 연령 제한, 웹소설, BL, 작가 키마님, …' 형식이다.
    제목에 [단행본] 이 붙으면 단행본, 아니면 '웹툰' · '웹소설' 표기를 유형으로 본다.
    """
    first = (text or '').strip().split('\n')[0]
    parts = [p.strip() for p in first.split(', ')]
    if len(parts) < 2 or parts[0] != '작품':
        return first.strip(), None
    name = parts[1]
    if '[단행본]' in name:
        return name, '단행본'
    for p in parts[2:]:
        if p in ('웹툰', '웹소설', '만화'):
            return name, p
    return name, None
