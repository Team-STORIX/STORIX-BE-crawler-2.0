import html
import json
import re
import time
import pickle

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, InvalidSessionIdException
from urllib3.exceptions import ReadTimeoutError as _DriverTimeoutError

from .base_crawler import PASS_THROUGH, BaseCrawler, HttpUnavailable, SessionExpiredError, pick_candidates
from config import RIDIBOOKS_COOKIE_FILE, RIDIBOOKS_LOGIN_URL, RIDIBOOKS_ID, RIDIBOOKS_PW

# 리디 브레드크럼 카테고리 텍스트 → 대표 장르(DB 표준값).
# 위에서부터 먼저 매칭되는 것을 채택 → 로판/BL/무협을 로맨스·판타지보다 앞에 둔다.
# (실제 리디 분류: 로판 e북/웹소설, BL 소설/웹소설/만화/웹툰, 무협 소설,
#  정통·퓨전·현대 판타지·판타지 웹소설, 로맨스 e북/웹소설, 라이트노벨 등)
_RIDI_GENRE_KEYWORDS = [
    ('로판', '로판'),
    ('BL', 'BL'),
    ('무협', '무협'),
    ('판타지', '판타지'),
    ('로맨스', '로맨스'),
    ('라이트노벨', '판타지'),   # 라이트노벨은 대개 판타지 계열 → 판타지로 귀속
    # 일반 소설 카테고리(예: "추리/미스터리/스릴러") 대응
    ('스릴러', '스릴러'),
    ('미스터리', '스릴러'),
    ('추리', '스릴러'),
    ('드라마', '드라마'),
    ('액션', '액션'),
]

# 웹툰/웹소설이 아닌 '일반 단행본' 카테고리 — 이런 작품은 크롤 대상에서 제외(스킵).
# 나라별 문학("일본 소설" 등)과 비소설 섹션(에세이/인문/자기계발 등)이 신호.
# 주의: '무협 소설' 같은 장르 웹소설은 여기 걸리지 않도록 나라명이 붙은 형태만 넣는다.
_RIDI_NON_TARGET_CAT_KEYWORDS = (
    '한국 소설', '일본 소설', '영미 소설', '중국 소설', '외국 소설',
    '프랑스 소설', '독일 소설', '러시아 소설', '북유럽 소설', '대만 소설',
    '에세이', '시/희곡', '인문', '자기계발', '경제/경영', '경제경영',
    '역사/문화', '종교/역학', '과학/공학', '자연/과학', '예술/대중문화',
    '가정/생활', '건강/취미', '컴퓨터/IT', '외국어', '잡지',
    '대학교재/전문서', '수험서/자격증', '초중고참고서',
    '유아', '어린이', '청소년',
)


def _ridi_is_general_lit(cat_texts: list[str]) -> bool:
    """브레드크럼 카테고리가 일반 단행본(비웹툰·비웹소설)이면 True."""
    blob = ' '.join(cat_texts)
    return any(kw in blob for kw in _RIDI_NON_TARGET_CAT_KEYWORDS)


def ridi_genre_and_type(cat_texts: list[str], section: str = '') -> tuple[str, str]:
    """카테고리 텍스트 → (대표 장르, works_type).
    예) ["판타지 웹소설", "현대 판타지"] → (판타지, 웹소설) / ["BL 웹툰", "BL 웹툰"] → (BL, 웹툰)
    장르는 매칭이 없으면 빈 값. 유형은 만화/웹툰 > 소설/노벨/e북 > og:section > 기본 웹툰."""
    cat_blob = ' '.join(cat_texts)
    genre = next((canonical for kw, canonical in _RIDI_GENRE_KEYWORDS if kw in cat_blob), '')
    if any(k in cat_blob for k in ('웹툰', '만화')):
        return genre, '웹툰'
    if any(k in cat_blob for k in ('웹소설', '소설', '노벨', 'e북')):
        return genre, '웹소설'
    if '소설' in section:
        return genre, '웹소설'
    return genre, '웹툰'


class RidibooksCrawler(BaseCrawler):
    REPORT_PLATFORM = 'ridibooks'
    LOGIN_URL_MARKERS = ('account/login',)
    # 연달아 열면 403 이 난다 (2026-10-09 연령 점검 중 확인, #6)
    REQUEST_INTERVAL = 2.0
    HTTP_403_IS_BLOCK = True


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

    def search_candidates(self, title: str) -> list[dict]:
        """리디 검색 결과 → 후보 목록 (일치도 순, 카드에 보이는 유형 힌트 포함).

        검색 결과 카드는 emotion 해시 클래스라 안정적인 셀렉터가 없어, `/books/<id>`
        링크(속성 기반)를 훑어 책ID 별로 (표시 제목, 카드 텍스트)를 모은다. 같은 책에
        썸네일 · 제목 두 앵커가 걸리므로 책ID로 합쳐 텍스트가 있는 쪽을 제목으로 쓴다.
        """
        import urllib.parse

        self.driver.get(f"https://ridibooks.com/search?q={urllib.parse.quote(title)}")
        try:
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/books/']"))
            )
        except TimeoutException:
            print(f"   ⚠️  '{title}' 검색 결과 로딩 실패 — 스킵")
            return []
        time.sleep(1)

        cards = self.driver.execute_script("""
            const out = {};
            for (const a of document.querySelectorAll("a[href*='/books/']")) {
                const id = (a.href.match(/\\/books\\/(\\d+)/) || [])[1];
                if (!id) continue;
                const text = ((a.getAttribute('title') || a.innerText || '').split('\\n')[0] || '').trim();
                let card = a;
                for (let i = 0; i < 6 && card.parentElement; i++) {
                    card = card.parentElement;
                    if (card.innerText.length > 40) break;
                }
                const prev = out[id];
                if (!prev) out[id] = {text, card: card.innerText, order: Object.keys(out).length};
                else if (!prev.text && text) prev.text = text;
            }
            return Object.entries(out).sort((a, b) => a[1].order - b[1].order)
                .map(([id, v]) => [id, v.text, v.card]);
        """) or []
        raw = [(f"https://ridibooks.com/books/{book_id}", text, ridi_type_hint(card))
               for book_id, text, card in cards if text]
        return self.rank_candidates(title, raw)

    def search_url_by_title(self, title: str, works_type: str | None = None) -> str | None:
        """제목으로 리디북스 작품 URL 검색. 원하는 유형이 있으면 그 유형 후보를 먼저 고른다."""
        picked = pick_candidates(self.search_candidates(title), works_type)
        if not picked:
            print(f"   ⚠️  '{title}'과 일치하는 검색 결과 없음 — 스킵")
            return None
        c = picked[0]
        print(f"   ↳ 검색 결과 ({c['kind']}): {c['url']} [{c['text']}]")
        return c['url']

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

    def crawl_detail_http(self, url: str) -> dict | None:
        src = self.http_get(url)
        if src is None:
            return None  # 내려간 작품
        return parse_ridi_book_page(src, url)

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
            thumb = ridi_real_cover(thumb, url)

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

            # 방법 0 (현재 레이아웃): 상단 정보의 저자 목록
            #   구조: <ul><li><div><a href="/author/ID">이름</a> 역할</div></li></ul>
            #   역할 텍스트(저자/글/그림/글그림/원작)는 앵커 뒤 텍스트노드로 붙는다.
            #   출판사는 /search 링크라 /author 링크만 보면 자연히 제외된다.
            #   추천 캐러셀에도 /author 링크가 있어, 저자 링크를 가진 '첫 ul'로 범위를 좁힌다.
            try:
                author_lis = self.driver.find_elements(
                    By.XPATH,
                    "(//ul[.//a[contains(@href,'/author/')]])[1]"
                    "//li[.//a[contains(@href,'/author/')]]"
                )
                for li in author_lis:
                    try:
                        a_el = li.find_element(By.CSS_SELECTOR, "a[href*='/author/']")
                        name = a_el.text.strip()
                        if not name:
                            continue
                        # li 전체 텍스트에서 이름을 뺀 나머지가 역할 (예: "유한려 저자" → "저자")
                        role = li.text.replace(name, '').strip()
                        role = role.replace('/', '').replace('·', '').replace(' ', '')
                        if '글' in role and '그림' in role:      # 글그림 / 글·그림
                            if not author: author = name
                            if not illustrator: illustrator = name
                        elif '그림' in role or '그린이' in role:
                            if not illustrator: illustrator = name
                        elif '원작' in role:
                            if not original_author: original_author = name
                        else:  # 저자/글/지은이/글쓴이/역할없음 → 글作家
                            if not author: author = name
                    except Exception:
                        continue
            except Exception:
                pass

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

            # 연령 등급: 페이지에 내려오는 책 데이터(is_adult_only · age_limit)와 공지("15세 이용가 안내")로 판정.
            # meta 'books:rating:value' 는 별점(4.8)이라 연령이 아니다 — 이걸 읽어서 15세가 전부 전체연령가로 들어갔다
            m = re.search(r'/books/(\d+)', self.driver.current_url)
            src = self.driver.page_source
            age = parse_ridi_age(src, m.group(1) if m else '')
            hashtags = parse_ridi_keywords(src)
            if not age:
                self._log.warning("리디 책 데이터를 읽지 못해 연령 판정 실패: %s", url)

            # 상단 브레드크럼 카테고리(/category/ 링크) 텍스트 — genre·works_type 판정에 함께 사용.
            #   예) ["판타지 웹소설", "현대 판타지"] / ["로맨스 e북", "하이틴", "현대물"]
            #   추천 캐러셀에도 /category 링크가 있어 '첫 ul'로 범위를 좁힌다.
            cat_texts: list[str] = []
            try:
                cat_els = self.driver.find_elements(
                    By.XPATH,
                    "(//ul[.//a[contains(@href,'/category/')]])[1]"
                    "//a[contains(@href,'/category/')]"
                )
                cat_texts = [c.text.strip() for c in cat_els if c.text.strip()]
            except Exception:
                pass
            # 일반 단행본(나라별 문학·에세이·인문 등)은 크롤 대상이 아니므로 스킵.
            if cat_texts and _ridi_is_general_lit(cat_texts):
                print(f"   ⏭️  일반문학/비대상 카테고리 — 스킵: {title} "
                      f"({' > '.join(cat_texts)})")
                return None

            section = ""
            try:
                section = self.driver.find_element(
                    By.CSS_SELECTOR, "meta[property='og:section'], meta[name='section']"
                ).get_attribute("content") or ""
            except Exception:
                pass
            genre, works_type = ridi_genre_and_type(cat_texts, section)

            return {
                "platform": "RIDIBOOKS",
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
                "source_url": url,
            }

        except PASS_THROUGH:
            raise
        except Exception as e:
            self._log.error("crawl_detail 실패 (%s): %s", url, e)
            return None


def parse_ridi_age(src: str, book_id: str) -> str:
    """리디 책 상세 페이지 HTML → 연령. 판정 못 하면 ''.

    - 이 책 데이터의 is_adult_only=true 또는 age_limit=19 → 18세 이용가
    - 페이지 공지(notices) 제목 "15세 이용가 안내" / "12세 이용가 안내" → 15세 / 12세
    - 책 데이터를 읽었는데 성인도 아니고 공지도 없으면 전체연령가 (2026-10-10 사용자 결정).
      BE 가 연령을 올리기만 해서(v2.6.6) 다른 플랫폼에서 들어온 15세 · 18세를 내리지 않는다
    - 책 데이터 자체를 못 읽으면 '' (판정 실패 — 페이지 구조가 바뀌었을 수 있다)
    """
    if not src or not book_id:
        return ''
    book = re.search(r'"id"\s*:\s*"%s"[^{}]*?"is_adult_only"\s*:\s*(true|false)' % re.escape(book_id), src)
    if not book:
        return ''
    limit = re.search(r'"age_limit"\s*:\s*"?(\d+)"?', book.group(0))
    if book.group(1) == 'true' or (limit and int(limit.group(1)) >= 19):
        return '18세 이용가'
    notices = re.search(r'"notices"\s*:\s*\[(.*?)\]', src, re.S)
    titles = ' '.join(re.findall(r'"title"\s*:\s*"([^"]*)"', notices.group(1))) if notices else ''
    for n in ('15', '12'):
        if f'{n}세 이용가' in titles or f'{n}세이용가' in titles:
            return f'{n}세 이용가'
    return '전체연령가'


def ridi_type_hint(card_text: str) -> str | None:
    """검색 결과 카드 글자 → 유형 힌트. '[e북] 마도조사 | … 해외 소설' · '마도조사 | … BL 웹툰' 형식.
    e북은 단행본, 웹툰 · 만화는 웹툰, 소설은 웹소설. 판단이 안 서면 None (상세에서 다시 판정)."""
    t = card_text or ''
    if '[e북]' in t:
        return '단행본'
    head = ' '.join(t.split('\n')[:3])  # 제목 · 작가/출판사/카테고리 · 권수 줄만 본다 (소개글 제외)
    if '웹툰' in head or '만화' in head:
        return '웹툰'
    if '소설' in head:
        return '웹소설'
    return None


# 성인 인증이 안 된 화면에서 표지 대신 나오는 가림 이미지
RIDI_ADULT_COVER = re.compile(r'ridicdn\.net/.*book_cover/cover_adult')


def ridi_real_cover(thumb: str, book_url: str) -> str:
    """가림 이미지면 책 ID 로 실제 표지 주소를 만든다. 리디 이미지 서버는 성인 책도 표지를 준다."""
    if not RIDI_ADULT_COVER.search(thumb or ''):
        return thumb
    m = re.search(r'/books/(\d+)', book_url or '')
    return f'https://img.ridicdn.net/cover/{m.group(1)}/xxlarge' if m else ''


# 리디가 키워드 목록에 같이 넣는 통계 · 가격 · 권수 · 판매 · 연재 상태 태그. 작품 내용이 아니라 해시태그로 쓰지 않는다
#   별점1000개이상 · 리뷰500개이상 · 평점4점이상 · 1만원~2만원 · 2만원초과 · 10000~15000원 · 5권이상 · 기다리면무료 · 연재완결
RIDI_META_KEYWORDS = re.compile(
    r'^(?:(?:별점|리뷰|평점)\d+.*|.*\d+\s*[만천]?\s*원(?:이상|이하|미만|초과)?|\d+\s*권(?:이상|이하|미만)?|\d+\s*~\s*\d+\s*권'
    r'|연재(?:완결|중)?|완결|ebook|전자책|만웹대여제|단행본|기다리면\s*무료|무료|대여|소장)$')


def parse_ridi_keywords(src: str) -> list[str]:
    """리디 상세 페이지 HTML → '이 작품의 키워드' 목록.

    페이지 데이터의 cell__BookDetailHomeKeywordTab.tabInfos[].name 을 쓴다. 메타 태그 keywords 는
    ebook · 전자책 · 별점1000개이상 · 작가명 · 출판사가 섞여 있어 쓰지 않는다.
    못 찾으면 화면의 키워드 버튼('#헤테로공')에서 읽는다.
    """
    if not src:
        return []
    m = re.search(r'"cell__BookDetailHomeKeywordTab"\s*:\s*\{.*?"tabInfos"\s*:\s*\[(.*?)\]', src, re.S)
    if m:
        names = re.findall(r'"name"\s*:\s*"((?:[^"\\]|\\.)*)"', m.group(1))
        names = [json.loads(f'"{n}"') for n in names]
    else:
        names = [html.unescape(n) for n in re.findall(r'<span>#<!-- -->([^<]+)</span>', src)]
    seen, out = set(), []
    for n in (x.strip() for x in names):
        if n and n not in seen and not RIDI_META_KEYWORDS.match(n):
            seen.add(n)
            out.append(n)
    return out


_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)

# 작가 묶음 제목 → 역할. 번역 · 감수 등은 작가로 넣지 않는다
_RIDI_SKIP_ROLES = ('번역', '옮긴이', '감수', '편집', '기획')


def _ridi_cells(src: str) -> dict:
    m = _NEXT_DATA.search(src or '')
    if not m:
        raise HttpUnavailable('__NEXT_DATA__ 없음')
    try:
        data = json.loads(m.group(1))
        cells = data['props']['pageProps']['sectionProps']['gridQuery']['riGrid']['grid']['cells']
    except (ValueError, KeyError, TypeError):
        raise HttpUnavailable('__NEXT_DATA__ 형식이 다름')
    out = {}
    for cell in cells:
        for key, value in cell.items():
            if key.startswith('cell__') and value is not None:
                out.setdefault(key[len('cell__'):], value)
    return out


def _ridi_intro_text(intro_html: str) -> str:
    """작품 소개 HTML → 화면 글자 (줄바꿈 유지, 줄마다 앞뒤 공백 정리)."""
    t = re.sub(r'<br\s*/?>', '\n', intro_html or '', flags=re.I)
    t = html.unescape(re.sub(r'<[^>]+>', '', t)).replace('\r\n', '\n').replace('\r', '\n')
    return '\n'.join(re.sub(r'[ \t]+', ' ', line).strip() for line in t.split('\n')).strip()


def parse_ridi_book_page(src: str, url: str) -> dict | None:
    """리디 책 상세 HTML(브라우저 없이 받은 것) → 수집 결과. 브라우저 crawl_detail 과 같은 값을 만든다.

    페이지에 같이 내려오는 __NEXT_DATA__ 의 BookDetailHomeHeader(제목 · 카테고리 · 작가 · 표지),
    BookDetailHomeBookIntroduction(소개)을 읽는다. 연령 · 키워드는 브라우저와 같은 함수로 읽는다.
    로그인하지 않아도 성인 작품 정보와 실제 표지가 내려온다 (2026-10-10 확인).
    일반 단행본 카테고리면 None. 형식이 다르면 HttpUnavailable (브라우저로 다시 연다).
    """
    cells = _ridi_cells(src)
    info = (cells.get('BookDetailHomeHeader') or {}).get('information') or {}
    title = ((info.get('title') or {}).get('title') or '').strip()
    if not title:
        raise HttpUnavailable('책 제목 없음')

    cat_texts = []
    for c in info.get('category') or []:
        for side in ('parentCategory', 'childCategory'):
            name = ((c.get(side) or {}).get('name') or '').strip()
            if name:
                cat_texts.append(name)
    if cat_texts and _ridi_is_general_lit(cat_texts):
        print(f"   ⏭️  일반문학/비대상 카테고리 — 스킵: {title} ({' > '.join(cat_texts)})")
        return None
    section = re.search(r'<meta[^>]+property="og:section"[^>]+content="([^"]*)"', src)
    genre, works_type = ridi_genre_and_type(cat_texts, section.group(1) if section else '')

    author = illustrator = original_author = ''
    for group in info.get('authorGroups') or []:
        role = (group.get('title') or '').replace('/', '').replace('·', '').replace(' ', '')
        names = [(a.get('name') or '').strip() for a in group.get('authors') or []]
        name = next((n for n in names if n), '')
        if not name or any(r in role for r in _RIDI_SKIP_ROLES):
            continue
        if '글' in role and '그림' in role:
            author = author or name
            illustrator = illustrator or name
        elif '그림' in role or '그린이' in role:
            illustrator = illustrator or name
        elif '원작' in role:
            original_author = original_author or name
        else:  # 저자 · 글 · 지은이
            author = author or name

    desc = _ridi_intro_text((cells.get('BookDetailHomeBookIntroduction') or {}).get('introductionHtml') or '')
    if not desc:
        og = re.search(r'<meta[^>]+property="og:description"[^>]+content="([^"]*)"', src)
        desc = html.unescape(og.group(1)).strip() if og else ''

    cover = (((cells.get('BookDetailHomeHeader') or {}).get('thumbnail') or {}).get('cover') or {}).get('xxlarge') or ''
    book_id = str(info.get('bookId') or '')
    age = parse_ridi_age(src, book_id)
    if not age:
        raise HttpUnavailable('연령 판정 실패')

    return {
        "platform": "RIDIBOOKS",
        "works_name": title,
        "artist_name": ", ".join(dict.fromkeys(n for n in (author, illustrator, original_author) if n)),
        "author": author,
        "illustrator": illustrator,
        "original_author": original_author,
        "age_classification": age,
        "description": desc,
        "genre": genre,
        "hashtags": parse_ridi_keywords(src),
        "thumbnail_url": ridi_real_cover(cover, url),
        "works_type": works_type,
        "source_url": url,
    }
