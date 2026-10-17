import time
import re
import urllib.parse

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from .naver_crawler import NaverCrawler
from .base_crawler import PASS_THROUGH, BaseCrawler, SessionExpiredError
from .titles import clean_work_name
from .classify import age_from_text


class NaverSeriesCrawler(NaverCrawler):
    REPORT_PLATFORM = 'naver_series'
    # 네이버 웹툰 작품 정보 API 는 웹툰 전용이다. 상세는 브라우저로 연다
    crawl_detail_http = BaseCrawler.crawl_detail_http

    """series.naver.com 네이버 시리즈 크롤러 (웹소설·웹툰 단행본).

    로그인은 NaverCrawler와 동일한 네이버 쿠키를 재사용한다.
    상세 URL 형식:
        웹소설: https://series.naver.com/novel/detail.series?productNo=...
        웹툰  : https://series.naver.com/comic/detail.series?productNo=...
    """


    _SEARCH_URL = 'https://series.naver.com/search/search.series?t=all&q={}'

    @staticmethod
    def _clean_title(raw: str) -> str:
        """'화산귀환 [독점]\\n(총 1939화/미완결)' → '화산귀환'. 상태 라벨은 다른 플랫폼과 같은 규칙으로 뗀다
        (titles.clean_work_name) — 판본 표기([19세 완전판])와 묶음 이름은 작품명이라 남긴다"""
        t = re.sub(r'\s*\(총[^)]*\)', '', raw.split('\n')[0])      # (총 …화/…) 제거
        return clean_work_name(t)

    def search_candidates(self, title: str) -> list[dict]:
        """네이버 시리즈 검색 결과 → 후보 목록 (일치도 순). 주소의 /novel/ · /comic/ 으로 유형을 안다.

        후보의 raw 는 라벨을 떼기 전 원래 제목, book 은 단행본 여부('[단행본]' · '총 N권').
        _clean_title 이 라벨을 떼서 text 로는 연재판 · 단행본을 못 가린다.
        """
        self.driver.get(self._SEARCH_URL.format(urllib.parse.quote(title)))
        time.sleep(2)
        raw, full = [], {}
        # 제목 링크만 선택 (class 예: 'N=a:nov.title' / 'N=a:com.title'). 이미지 링크는 제외.
        for a in self.driver.find_elements(By.CSS_SELECTOR, "a[href*='detail.series'][class*='title']"):
            href = a.get_attribute('href') or ''
            if 'detail.series' not in href:
                continue
            original = (a.get_attribute('title') or a.text or '').strip()
            text = self._clean_title(original)
            if text:
                raw.append((href, text, '웹소설' if '/novel/' in href else '웹툰' if '/comic/' in href else None))
                full[href] = original
        cands = self.rank_candidates(title, raw)
        for c in cands:
            c['raw'] = full.get(c['url'], '').replace('\n', ' ')
            c['book'] = '[단행본]' in c['raw'] or bool(re.search(r'총\s*\d+\s*권', c['raw']))
            if c['book']:
                # 다른 플랫폼(리디 · 카카오 · 미스터블루)처럼 단행본은 유형 힌트로 알린다 — e북 연령 비우기 ·
                # 단행본은 연재판 다음 순서(pick_candidates)가 시리즈에도 똑같이 걸린다 (#121)
                c['type_hint'] = '단행본'
        return cands

    def _parse_info(self, lines: list[str]) -> dict:
        """`.end_info` 텍스트 라인에서 작가/그림/원작/연령을 추출한다.

        예) ['연재중', '무협', '글비가', '출판사러프미디어', '전체 이용가']
            ['연재중', '무협', '글비가', '그림ARCHE, LICO', '출판사네이버웹툰', '15세 이용가']
        """
        author = illustrator = original_author = ''
        # 연령 줄이 없으면 ''(판정 못 함). 전체연령가로 단정하면 새 작품은 틀린 값으로, 기존 작품은 덮어써져 들어간다
        age = ''
        for ln in lines:
            if ln.startswith('글'):
                author = ln[1:].strip()
            elif ln.startswith('그림'):
                illustrator = ln[2:].strip()
            elif ln.startswith('원작'):
                original_author = ln[2:].strip()
            elif '이용가' in ln or '이용불가' in ln or '19' in ln:
                age = age_from_text(ln) or age
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

        except PASS_THROUGH:
            raise
        except Exception as e:
            self._log.error("crawl_detail 실패 (%s): %s", url, e)
            return None
