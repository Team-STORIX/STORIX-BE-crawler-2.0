import time
import re
import urllib.parse

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import InvalidSessionIdException
from urllib3.exceptions import ReadTimeoutError as _DriverTimeoutError

from .naver_crawler import NaverCrawler
from .base_crawler import SessionExpiredError


class NaverSeriesCrawler(NaverCrawler):
    """series.naver.com 네이버 시리즈 크롤러 (웹소설·웹툰 단행본).

    로그인은 NaverCrawler와 동일한 네이버 쿠키를 재사용한다.
    상세 URL 형식:
        웹소설: https://series.naver.com/novel/detail.series?productNo=...
        웹툰  : https://series.naver.com/comic/detail.series?productNo=...
    """

    _platform = 'naver_series'

    _SEARCH_URL = 'https://series.naver.com/search/search.series?t=all&q={}'

    @staticmethod
    def _clean_title(raw: str) -> str:
        """'화산귀환 [독점]\\n(총 1939화/미완결)' → '화산귀환'."""
        t = raw.split('\n')[0]
        t = re.sub(r'\s*\[[^\]]*\]', '', t)      # [독점] [단행본] [완결] 등 제거
        t = re.sub(r'\s*\(총[^)]*\)', '', t)      # (총 …화/…) 제거
        return t.strip()

    def search_url_by_title(self, title: str) -> str | None:
        """제목으로 네이버 시리즈 상세 URL 검색. 웹소설(/novel/)을 우선 반환."""
        self.driver.get(self._SEARCH_URL.format(urllib.parse.quote(title)))
        time.sleep(2)

        norm_title = self._norm_title(title)

        # 제목 링크만 선택 (class 예: 'N=a:nov.title' / 'N=a:com.title'). 이미지 링크는 제외.
        links = self.driver.find_elements(
            By.CSS_SELECTOR, "a[href*='detail.series'][class*='title']"
        )

        exact_novel = exact_comic = partial_novel = partial_comic = None
        fuzzy_novel = fuzzy_comic = None
        fuzzy_novel_score = fuzzy_comic_score = 0.0
        for a in links:
            href = a.get_attribute('href') or ''
            if 'detail.series' not in href:
                continue
            text = self._clean_title((a.get_attribute('title') or a.text or '').strip())
            if not text:
                continue
            is_novel = '/novel/' in href
            norm_text = self._norm_title(text)

            if norm_text == norm_title:
                if is_novel and not exact_novel:
                    exact_novel = (href, text)
                elif not is_novel and not exact_comic:
                    exact_comic = (href, text)
            elif norm_title in norm_text or norm_text in norm_title:
                if is_novel and not partial_novel:
                    partial_novel = (href, text)
                elif not is_novel and not partial_comic:
                    partial_comic = (href, text)
            else:
                ratio = self._title_ratio(norm_title, norm_text)
                if ratio >= self.TITLE_FUZZY_THRESHOLD:
                    if is_novel and ratio > fuzzy_novel_score:
                        fuzzy_novel_score, fuzzy_novel = ratio, (href, text)
                    elif not is_novel and ratio > fuzzy_comic_score:
                        fuzzy_comic_score, fuzzy_comic = ratio, (href, text)

        hit = (exact_novel or exact_comic or partial_novel or partial_comic
               or fuzzy_novel or fuzzy_comic)
        if hit:
            if hit in (exact_novel, exact_comic):
                kind = '정확'
            elif hit in (partial_novel, partial_comic):
                kind = '부분'
            else:
                score = fuzzy_novel_score if hit is fuzzy_novel else fuzzy_comic_score
                kind = f'유사 {score:.0%}'
            print(f"   ↳ 검색 결과 ({kind}): {hit[0]} [{hit[1]}]")
            return hit[0]

        print(f"   ⚠️  '{title}' 네이버 시리즈 검색 결과 없음 — 스킵")
        return None

    def _parse_info(self, lines: list[str]) -> dict:
        """`.end_info` 텍스트 라인에서 작가/그림/원작/연령을 추출한다.

        예) ['연재중', '무협', '글비가', '출판사러프미디어', '전체 이용가']
            ['연재중', '무협', '글비가', '그림ARCHE, LICO', '출판사네이버웹툰', '15세 이용가']
        """
        author = illustrator = original_author = ''
        age = '전체연령가'
        for ln in lines:
            if ln.startswith('글'):
                author = ln[1:].strip()
            elif ln.startswith('그림'):
                illustrator = ln[2:].strip()
            elif ln.startswith('원작'):
                original_author = ln[2:].strip()
            elif '이용가' in ln or '이용불가' in ln or '19' in ln:
                if any(x in ln for x in ['19', '청소년 이용불가', '성인']):
                    age = '18세 이용가'
                elif '15' in ln:
                    age = '15세 이용가'
                elif '12' in ln:
                    age = '12세 이용가'
                elif '전체' in ln:
                    age = '전체연령가'
        return {
            'author': author,
            'illustrator': illustrator,
            'original_author': original_author,
            'age_classification': age,
        }

    def crawl_detail(self, url: str) -> dict | None:
        try:
            self.driver.get(url)

            if "nid.naver.com" in self.driver.current_url:
                raise SessionExpiredError(f"네이버 로그인 리다이렉트 감지: {url}")

            wait = WebDriverWait(self.driver, 15)
            try:
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".end_head h2")))
            except Exception:
                self._log.warning("페이지 로딩 시간 초과: %s", url)
                return None

            works_type = "웹툰" if "/comic/" in url else "웹소설"

            # 제목: h2 안의 연령 배지(<span class="ico_age2 n19_v2">19</span>)를 DOM에서
            # 떼어낸 뒤 읽는다. '191305호'처럼 배지 뒤가 숫자로 이어지면 문자열
            # 후처리로는 '19'만 못 떼므로, 배지 요소 자체를 제거해야 한다.
            title = ""
            try:
                h2 = self.driver.find_element(By.CSS_SELECTOR, ".end_head h2")
                raw = self.driver.execute_script(
                    "var h = arguments[0].cloneNode(true);"
                    "h.querySelectorAll('[class*=\"ico_age\"]').forEach(function(e){e.remove();});"
                    "return h.textContent;",
                    h2,
                )
                title = self._clean_title((raw or "").strip())
            except Exception:
                pass
            if not title:
                return None

            # .end_info: 장르(첫 링크) + 작가/그림/원작/연령(텍스트 라인)
            genre = ""
            try:
                genre = self.driver.find_element(
                    By.CSS_SELECTOR, ".end_info a"
                ).text.strip()
            except Exception:
                pass
            try:
                info_el = self.driver.find_element(By.CSS_SELECTOR, ".end_info")
                lines = [
                    ln.strip()
                    for ln in (info_el.text or "").split('\n')
                    if ln.strip()
                ]
            except Exception:
                lines = []
            parsed = self._parse_info(lines)

            # 설명: .end_dsc 안의 ._synopsis 중 가장 긴 텍스트(더보기 확장본 포함)
            desc = ""
            try:
                syns = self.driver.find_elements(By.CSS_SELECTOR, ".end_dsc ._synopsis")
                texts = []
                for s in syns:
                    t = (self.driver.execute_script("return arguments[0].innerText", s) or "")
                    texts.append(t)
                if texts:
                    desc = max(texts, key=len)
            except Exception:
                pass
            if not desc:
                try:
                    _el = self.driver.find_element(By.CSS_SELECTOR, ".end_dsc")
                    desc = self.driver.execute_script("return arguments[0].innerText", _el) or ""
                except Exception:
                    pass
            desc = desc.replace('\xa0', ' ')
            desc = re.sub(r'\s*(더보기|접기)\s*$', '', desc).strip()

            # 썸네일: 좌측 커버 이미지
            thumb = ""
            for sel in [".aside a.pic_area img", ".pic_area img", ".end_head img"]:
                try:
                    src = self.driver.find_element(By.CSS_SELECTOR, sel).get_attribute("src")
                    if src:
                        thumb = src
                        break
                except Exception:
                    pass

            return {
                "platform": "NAVER_SERIES",
                "works_name": title,
                "artist_name": parsed['author'],
                "author": parsed['author'],
                "illustrator": parsed['illustrator'],
                "original_author": parsed['original_author'],
                "age_classification": parsed['age_classification'],
                "description": desc,
                "genre": genre,
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
