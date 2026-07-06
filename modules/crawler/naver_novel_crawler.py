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
