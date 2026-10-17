import json
import re
import time
import random

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException

from .base_crawler import PASS_THROUGH, AuthExpiredError, BaseCrawler, manual_login_timeout, HttpUnavailable, SessionExpiredError
from .cookies import apply_cookies, write_cookies
from .classify import age_from_text
from .titles import clean_work_name
from review.landing import is_pre_contract_url

from config import NAVER_COOKIE_FILE, NAVER_ID, NAVER_PW

# 작품 정보 API. 상세 페이지가 화면에 그리는 값(제목 · 작가 역할 · 연령 · 소개 · 태그 · 표지)을 그대로 준다
TITLE_INFO_API = 'https://comic.naver.com/api/article/list/info?titleId={}'
_ARTIST_ROLES = {'ARTIST_WRITER': 'author', 'ARTIST_PAINTER': 'illustrator', 'ARTIST_NOVEL_ORIGIN': 'original_author'}


def parse_naver_title_info(info: dict, url: str) -> dict | None:
    """작품 정보 API 응답 → 수집 결과. 브라우저 crawl_detail 과 같은 값을 만든다.
    도전만화 · 베스트도전(webtoonLevelCode 가 WEBTOON 아님)은 None. 응답 형식이 다르면 HttpUnavailable."""
    title = (info.get('titleName') or '').strip()
    if not title or 'curationTagList' not in info:
        raise HttpUnavailable('작품 정보 응답 형식이 다름')
    if info.get('webtoonLevelCode') != 'WEBTOON':
        return None

    roles = {'author': '', 'illustrator': '', 'original_author': ''}
    names = []
    for artist in info.get('communityArtists') or []:
        name = (artist.get('name') or '').strip()
        if not name:
            continue
        if name not in names:
            names.append(name)
        for code in artist.get('artistTypeList') or []:
            field = _ARTIST_ROLES.get(code)
            if field and not roles[field]:
                roles[field] = name

    # 화면과 같이 첫 태그가 장르, 나머지가 해시태그
    tags = [t for tag in info.get('curationTagList') or [] if (t := (tag.get('tagName') or '').strip().lstrip('#'))]
    return {
        "platform": "NAVER_WEBTOON",
        "works_name": title,
        "artist_name": ', '.join(names),
        **roles,
        "age_classification": age_from_text((info.get('age') or {}).get('description')),
        "description": (info.get('synopsis') or '').strip(),
        "genre": tags[0] if tags else '',
        "hashtags": tags[1:],
        "thumbnail_url": info.get('sharedThumbnailUrl') or info.get('thumbnailUrl') or '',
        "works_type": "웹툰",
        "source_url": url,
    }


# 쿠키 로그인 확인용 성인 작품 (네이버 시리즈 「절대역」). 로그인 · 성인 인증이 안 되면 nid.naver.com 로 넘어간다
ADULT_CHECK_URL = 'https://series.naver.com/novel/detail.series?productNo=9579028'


class NaverCrawler(BaseCrawler):
    REPORT_PLATFORM = 'naver_webtoon'
    LOGIN_URL_MARKERS = ('nid.naver.com',)


    def _is_logged_in(self) -> bool:
        """NID_AUT 쿠키 존재 여부로 네이버 로그인 확인 (XPATH보다 신뢰성 높음)."""
        self.driver.get("https://www.naver.com")
        time.sleep(1)
        return any(c['name'] == 'NID_AUT' for c in self.driver.get_cookies())

    def _wait_for_manual_login(self, timeout_sec: int = 180) -> bool:
        """2단계 폴링: 1) nid.naver.com 이탈 감지 → 2) comic.naver.com에서 성인 인증 완료 대기."""
        timeout_sec = manual_login_timeout(timeout_sec)
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
                write_cookies(NAVER_COOKIE_FILE, self.driver.get_cookies())
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
            # 넣은 쿠키라 NID_AUT 는 늘 있어 만료도 통과했다. 다른 플랫폼처럼 쿠키 로그인 판정(성인 작품)을 쓴다 (#121)
            if self.login_with_cookies():
                print("✅ 쿠키 로그인 성공")
                return True
            print("⚠️ 쿠키 만료 또는 로그인 실패. 다음 수단을 시도합니다.")

        if self._login_with_credentials():
            return True

        import os
        if os.environ.get("DOCKER_ENV") == "true":
            raise AuthExpiredError(  # 서버(Docker)는 로그인 화면을 못 띄운다 — 세션 만료로 알린다 (#77)
                "Docker 환경에서는 수동 로그인 불가.\n"
                "NAVER_ID/NAVER_PW 환경변수를 설정하거나 sessions/naver_cookies.pkl을 생성하세요."
            )

        self.driver.get("https://nid.naver.com/nidlogin.login")
        time.sleep(2)
        if self._wait_for_manual_login():
            print("✅ 로그인 확인됨. 쿠키를 저장합니다.")
            write_cookies(NAVER_COOKIE_FILE, self.driver.get_cookies())
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
            apply_cookies(self.driver, NAVER_COOKIE_FILE, domain='.naver.com')
            # 넣은 쿠키라 NID_AUT 는 늘 있다. 성인 작품이 로그인 화면으로 넘어가지 않는지로 본다 (#98)
            self.driver.get(ADULT_CHECK_URL)
            time.sleep(2)
            return not self.is_login_page()
        except Exception as e:
            print(f"⚠️ 워커 쿠키 로그인 실패: {e}")
            return False

    def search_candidates(self, title: str) -> list[dict]:
        """네이버 웹툰 검색 API → 후보 목록 (일치도 순). 브라우저를 쓰지 않는다."""
        import json
        import urllib.parse
        import urllib.request

        q = urllib.parse.quote(title.replace('%', ' ').strip())
        req = urllib.request.Request(f'https://comic.naver.com/api/search/all?keyword={q}',
                                     headers={'User-Agent': 'Mozilla/5.0'})
        data = json.load(urllib.request.urlopen(req, timeout=10))
        raw = [(f"https://comic.naver.com/webtoon/list?titleId={x.get('titleId')}", x.get('titleName') or '', '웹툰')
               for x in data['searchWebtoonResult']['searchViewList']]
        return self.rank_candidates(title, raw)

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

    def crawl_detail_http(self, url):
        m = re.search(r'titleId=(\d+)', url or '')
        if not m or is_pre_contract_url(url):
            raise HttpUnavailable('작품 번호 없는 주소')
        body = self.http_get(TITLE_INFO_API.format(m.group(1)), headers={'Accept': 'application/json'})
        if body is None:
            return None  # 내려간 작품
        try:
            info = json.loads(body)
        except ValueError:
            raise HttpUnavailable('JSON 아님')
        return parse_naver_title_info(info, url)

    def crawl_detail(self, url):
        try:
            self.driver.get(url)
            if "nid.naver.com" in self.driver.current_url:
                raise SessionExpiredError(f"네이버 로그인 리다이렉트 감지: {url}")

            # 내려간 작품은 홈(/index)으로 리다이렉트됨 → 10초 대기 없이 즉시 스킵
            if 'titleId=' not in self.driver.current_url:
                self._log.warning("작품 페이지 아님(내려간 작품 추정): %s → %s", url, self.driver.current_url)
                return None

            # 도전만화·베스트도전은 정식 계약 전 작품이라 수집하지 않는다
            if is_pre_contract_url(self.driver.current_url):
                self._log.info("도전만화·베스트도전 작품이라 건너뜀: %s", self.driver.current_url)
                return None

            wait = WebDriverWait(self.driver, 10)
            
            # 제목 추출 및 전처리
            title_raw = wait.until(EC.visibility_of_element_located((By.XPATH, '//*[@id="content"]/div[1]/div/h2'))).text
            title = clean_work_name(title_raw).replace("휴재", "").strip()

            # 작가 영역: 작가 한 명당 span 하나 (`이름 ∙ 역할` 구조, 역할 = 글|그림|원작|글/그림)
            # 역할 라벨은 빼고 이름만 모으고, 역할별 필드도 채운다.
            author = illustrator = original_author = ""
            artist_names = []
            for _span in self.driver.find_elements(By.XPATH, '//*[@id="content"]/div[1]/div/div[1]/span'):
                _text = _span.text.strip()
                if not _text or '∙' not in _text:
                    continue
                _name_part, _role = _text.rsplit('∙', 1)
                _links = _span.find_elements(By.TAG_NAME, 'a')
                name = (_links[0].text if _links else _name_part).strip()
                _role = _role.strip()
                if not name:
                    continue
                if name not in artist_names:
                    artist_names.append(name)
                if '글' in _role and not author: author = name
                if '그림' in _role and not illustrator: illustrator = name
                if '원작' in _role and not original_author: original_author = name
            artist = ', '.join(artist_names)
            _desc_el = self.driver.find_element(By.XPATH, '//*[@id="content"]/div[1]/div/div[2]/p')
            desc = (self.driver.execute_script("return arguments[0].innerText", _desc_el) or "").strip()
            genre = self.driver.find_element(By.XPATH, '//*[@id="content"]/div[1]/div/div[2]/div/div/a[1]').text.lstrip('#').strip()
            age = age_from_text(self.driver.find_element(By.XPATH, '//*[@id="content"]/div[1]/div/div[1]/em').text.strip().split('∙')[-1])
            
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
                "platform": "NAVER_WEBTOON",
                "works_name": title,
                "artist_name": artist,
                "author": author,
                "illustrator": illustrator,
                "original_author": original_author,
                "age_classification": age,
                "description": desc, 
                "genre": genre,
                "hashtags": hashtags, 
                "thumbnail_url": thumb, 
                "works_type": "웹툰", 
                "source_url": url
            }
        
        except PASS_THROUGH:
            raise
        except Exception as e:
            self._log.warning("crawl_detail 실패 (%s): %s", url, e)
            return None
