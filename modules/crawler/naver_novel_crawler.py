import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import InvalidSessionIdException
from urllib3.exceptions import ReadTimeoutError as _DriverTimeoutError

from .naver_crawler import NaverCrawler
from .base_crawler import SessionExpiredError


class NaverNovelCrawler(NaverCrawler):
    """novel.naver.com 웹소설 크롤러. 로그인은 NaverCrawler와 동일한 쿠키 재사용."""

    _platform = 'naver_novel'

    # 검색결과 카드의 리그 구분 (href 경로) → (우선순위, 표기). 낮은 순위가 우선.
    # 정식 웹소설을 항상 우선하고, 정식이 없을 때만 베스트리그·챌린지리그로 폴백한다.
    _LEAGUE_RANK = {'/webnovel/list': 0, '/best/list': 1, '/challenge/list': 2}
    _LEAGUE_NAME = {'/webnovel/list': '정식', '/best/list': '베스트리그', '/challenge/list': '챌린지리그'}

    def search_url_by_title(self, title: str) -> str | None:
        """제목으로 네이버 웹소설(novel.naver.com) URL 검색.

        검색결과 영역(.component_section)만 스코프해 우측 랭킹 사이드바(.league_wrap)를
        배제한다. 리그 우선순위(정식 > 베스트리그 > 챌린지리그) → 매치품질(정확 > 부분 >
        유사) 순으로 가장 좋은 후보를 고른다. 어느 리그에도 매치가 없으면 None.
        """
        import urllib.parse

        self.driver.get(
            f"https://novel.naver.com/search?keyword={urllib.parse.quote(title)}"
        )
        time.sleep(2)

        norm_title = self._norm_title(title)

        # 검색결과 카드 링크만 (사이드바 랭킹은 .league_wrap 아래라 제외됨).
        anchors = self.driver.find_elements(
            By.CSS_SELECTOR, ".component_section a[href*='novelId=']"
        )

        # 후보 선택 키: (리그순위, 품질, -유사도) — 튜플이 작을수록 우선.
        # 품질 0=정확, 1=부분, 2=유사(≥임계값).
        best_key = None
        best = None  # (href, text, league_path, quality, ratio)
        for a in anchors:
            href = a.get_attribute('href') or ''
            league = next((p for p in self._LEAGUE_RANK if p in href), None)
            if league is None:
                continue

            try:
                text = a.find_element(By.CSS_SELECTOR, '.title').text.strip()
            except Exception:
                text = (a.get_attribute('title') or a.text or '').strip()
            if not text:
                continue
            norm_text = self._norm_title(text)

            # 부분일치(substring)는 정식 웹소설에만 허용한다. 베스트/챌린지리그엔 원작
            # 제목을 포함하는 2차창작·팬픽이 많아, substring 을 허용하면 오탐이 된다.
            # (예: '전지적 독자 시점' → '[전지적 독자 시점/전독시 팬픽] 피투성이')
            # 아마추어 리그는 완전일치 + 유사도(길이 민감)만 인정한다.
            is_official = league == '/webnovel/list'
            if norm_text == norm_title:
                quality, ratio = 0, 1.0
            elif is_official and (norm_title in norm_text or norm_text in norm_title):
                quality, ratio = 1, 0.0
            else:
                ratio = self._title_ratio(norm_title, norm_text)
                if ratio < self.TITLE_FUZZY_THRESHOLD:
                    continue
                quality = 2

            key = (self._LEAGUE_RANK[league], quality, -ratio)
            if best_key is None or key < best_key:
                best_key = key
                best = (href, text, league, quality, ratio)

        if best:
            href, text, league, quality, ratio = best
            league_name = self._LEAGUE_NAME[league]
            kind = {0: '정확', 1: '부분'}.get(quality, f'유사 {ratio:.0%}')
            print(f"   ↳ 검색 결과 ({league_name}·{kind}): {href} [{text}]")
            return href

        print(f"   ⚠️  '{title}' 네이버 웹소설 검색 결과 없음 — 스킵")
        return None

    def get_genre_urls(self, genre_url: str) -> list[str]:
        print(f"📂 URL 수집 시작: {genre_url}")
        self.driver.get(genre_url)
        time.sleep(2)

        prev_count = 0
        stuck = 0
        while True:
            elems = self.driver.find_elements(
                By.XPATH, "//a[contains(@href,'novelId=')]"
            )
            curr = len(elems)
            if curr > prev_count:
                prev_count = curr
                stuck = 0
                print(f"  └─ {curr}개 수집 중...")
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1.5)
            else:
                stuck += 1
                if stuck >= 3:
                    break
                self.driver.execute_script("window.scrollBy(0, -500);")
                time.sleep(0.5)
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2)

        elems = self.driver.find_elements(
            By.XPATH, "//a[contains(@href,'novelId=')]"
        )
        seen: set[str] = set()
        urls: list[str] = []
        for e in elems:
            href = (e.get_attribute('href') or '').split('?')[0] + '?' + (e.get_attribute('href') or '').split('?')[-1] if '?' in (e.get_attribute('href') or '') else e.get_attribute('href') or ''
            if 'novelId=' in href and href not in seen:
                seen.add(href)
                urls.append(href)

        print(f"✅ {len(urls)}개 URL 수집 완료")
        return urls

    def crawl_detail(self, url: str) -> dict | None:
        try:
            self.driver.get(url)

            if "nid.naver.com" in self.driver.current_url:
                raise SessionExpiredError(f"네이버 로그인 리다이렉트 감지: {url}")

            wait = WebDriverWait(self.driver, 15)
            try:
                wait.until(EC.presence_of_element_located((By.TAG_NAME, "h2")))
            except Exception:
                self._log.warning("페이지 로딩 시간 초과: %s", url)
                return None

            # 제목
            title = ""
            for sel in ["h2.title", "h2.tit", ".title_area h2", ".tit_area h2", "h2", "h1"]:
                try:
                    t = self.driver.find_element(By.CSS_SELECTOR, sel).text.strip()
                    if t:
                        title = t
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

            # 작가: href에 target=author가 있는 링크가 가장 신뢰할 수 있는 셀렉터
            author = ""
            for sel in ["a[href*='target=author']", ".author em", ".writer em", ".author a", ".writer a", ".author", ".writer"]:
                try:
                    t = self.driver.find_element(By.CSS_SELECTOR, sel).text.strip()
                    if t:
                        author = t
                        break
                except Exception:
                    pass
            if not author:
                try:
                    author = self.driver.find_element(
                        By.CSS_SELECTOR, "meta[name='author']"
                    ).get_attribute("content") or ""
                except Exception:
                    pass

            # 설명: #summaryText가 "더보기" 링크를 제외한 본문만 담고 있음
            desc = ""
            for sel in ["#summaryText", ".synopsis", ".des", ".story", ".synopsis_wrap", ".introduce"]:
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

            # 장르: .info_group의 .item 중 링크가 없고 연재 상태가 아닌 첫 번째 텍스트
            _STATUS_WORDS = {"자유연재", "완결", "휴재", "연재중", "연재", "독점", "기다무"}
            genre = ""
            hashtags: list[str] = []
            try:
                items = self.driver.find_elements(By.CSS_SELECTOR, ".info_group .item")
                for item in items:
                    if item.find_elements(By.TAG_NAME, "a"):
                        continue
                    t = item.text.strip()
                    if t and t not in _STATUS_WORDS:
                        genre = t
                        break
            except Exception:
                pass

            # 해시태그: .tag_collection의 a.tag 요소들
            try:
                tag_els = self.driver.find_elements(By.CSS_SELECTOR, ".tag_collection a.tag")
                hashtags = [el.text.strip().lstrip('#') for el in tag_els if el.text.strip()]
            except Exception:
                pass

            # fallback: 장르/해시태그 둘 다 못 찾은 경우 기존 방식 시도
            if not genre and not hashtags:
                for sel in [".genre_area a", ".badge_list a", ".category a", ".genre a", ".tag_list a"]:
                    try:
                        els = self.driver.find_elements(By.CSS_SELECTOR, sel)
                        tags = [el.text.strip().lstrip('#') for el in els if el.text.strip()]
                        if tags:
                            genre = tags[0]
                            hashtags = tags[1:]
                            break
                    except Exception:
                        pass

            # 썸네일
            thumb = ""
            try:
                thumb = self.driver.find_element(
                    By.CSS_SELECTOR, "meta[property='og:image']"
                ).get_attribute("content") or ""
            except Exception:
                pass

            # 연령
            age = "전체연령가"
            src = self.driver.page_source
            if any(x in src for x in ["19세 이용가", "청소년 이용불가", "성인 인증"]):
                age = "18세 이용가"
            elif "15세 이용가" in src:
                age = "15세 이용가"
            elif "12세 이용가" in src:
                age = "12세 이용가"

            return {
                "platform": "NAVER_NOVEL",
                "works_name": title,
                "artist_name": author,
                "author": author,
                "illustrator": "",
                "original_author": "",
                "age_classification": age,
                "description": desc,
                "genre": genre,
                "hashtags": hashtags,
                "thumbnail_url": thumb,
                "works_type": "웹소설",
                "source_url": url,
            }

        except (InvalidSessionIdException, SessionExpiredError, _DriverTimeoutError):
            raise
        except Exception as e:
            self._log.error("crawl_detail 실패 (%s): %s", url, e)
            return None
