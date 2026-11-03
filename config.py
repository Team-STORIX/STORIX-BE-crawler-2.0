import os
import sys
from dotenv import load_dotenv
import pathlib

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

load_dotenv()

BASE_DIR = pathlib.Path(__file__).parent
SESSIONS_DIR = BASE_DIR / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
NAVER_COOKIE_FILE = SESSIONS_DIR / "naver_cookies.pkl"
KAKAO_COOKIE_FILE = SESSIONS_DIR / "kakao_cookies.pkl"
OUTPUT_DIR = BASE_DIR / "output"
# 이번 실행의 날짜 (output/<날짜>/). 처음 정한 날짜를 끝까지 쓴다 — 수집이 자정을 넘기면 수집 파일과
# 적재 · 리포트가 다른 날짜 폴더를 봤다 (#121). 시간대는 실행 환경 그대로 (서버 컨테이너는 UTC)
RUN_DATE = __import__('datetime').datetime.now().strftime('%Y-%m-%d')
RUN_DIR = OUTPUT_DIR / RUN_DATE

# ---- 네이버 웹툰 설정 ----

# 1차 크롤링 URL 및 장르 목록
BASE_URL = 'https://comic.naver.com/webtoon?tab=genre&genre='
GENRES = ["PURE", "FANTASY", "DAILY", "로판", "HISTORICAL"]

# 2차 크롤링 URL (daily - 인기순)
DAILY_PLUS_URL = 'https://comic.naver.com/webtoon?tab=dailyPlus'

# 2차 크롤링 URL + 수집 개수 제한 (completed - 완결)
COMPLETED_URL = 'https://comic.naver.com/webtoon?tab=finish'

NAVER_NEW_URL = 'https://comic.naver.com/webtoon?tab=new'
# 네이버 시리즈 신작 (목록 url, 최대). 최신순 25개씩 (#161)
NAVER_SERIES_NEW_TARGETS = [
    ("https://series.naver.com/comic/recentList.series", 100),
    ("https://series.naver.com/novel/recentList.series", 100),
]

# 신작 주간 수집 요일 (월=0). 서버는 자정에 켜져 01:00 에 꺼져서 하루에 플랫폼 하나씩 (#158).
# 일요일은 비워 둔다 — 월요일 주간 작품 추가 알림이 한 주 수집분을 다 담고, 랭킹(월 1회) 자리로 쓴다
WEEKLY_NEW_SCHEDULE = {
    0: 'naver_webtoon',
    1: 'kakao_page',
    2: 'ridibooks',
    3: 'naver_series',
    4: 'lezhin',         # #162
    5: 'mrblue',         # #163
}

# ---- 카카오 웹툰 설정 ----
KAKAO_LOGIN_URL = "https://accounts.kakao.com/login/?continue=https%3A%2F%2Fkauth.kakao.com%2Foauth%2Fauthorize%3Fclient_id%3D49bbb48c5fdb0199e5da1b89de359484%26state%3Dhttps%25253A%25252F%25252Fpage.kakao.com%25252Fmenu%25252F10010%25252Fscreen%25252F93%26redirect_uri%3Dhttps%253A%252F%252Fpage.kakao.com%252Frelay%252Flogin%26response_type%3Dcode%26auth_tran_id%3DW3lvNUKSoQz6HLrxqft_Qn0McwWmXpOWQ7Zo.f_58sE5Hx7anOVDmu5vgoIS%26ka%3Dsdk%252F2.1.0%2520os%252Fjavascript%2520sdk_type%252Fjavascript%2520lang%252Fko-KR%2520device%252FMacIntel%2520origin%252Fhttps%25253A%25252F%25252Fpage.kakao.com%26is_popup%3Dfalse%26through_account%3Dtrue&talk_login=hidden#login"
# 신작 탭 (url, 유형). 카카오는 탭 번호를 바꾼다 — 없는 번호면 기본 탭('지금핫한')이 열려 인기작을 신작으로 긁었다.
# 열린 화면에서 선택된 탭 이름이 KAKAO_NEW_TAB 이 아니면 수집하지 않는다 (#159)
KAKAO_NEW_TAB = '오늘신작'
KAKAO_NEW_URLS = [
    ("https://page.kakao.com/menu/10010/screen/53", "웹툰"),
    ("https://page.kakao.com/menu/10011/screen/101", "웹소설"),
]

# initial 크롤링 대상 섹션 (url, 레이블) — 순서대로 수집
KAKAO_INITIAL_SECTIONS = [
    ("https://page.kakao.com/menu/10010/screen/93",                         "판타지 TOP 300"),
    ("https://page.kakao.com/menu/10010/screen/62",                         "무협 TOP 300"),
    ("https://page.kakao.com/landing/ranking/10/119?ranking_type=monthly",  "BL 웹툰 월간 TOP 300"),
    ("https://page.kakao.com/landing/series/poster/page/landing/4415",      "1억뷰+ 웹툰"),
    ("https://page.kakao.com/landing/ranking/11/87?ranking_type=monthly",   "무협 웹소설 월간 TOP 300"),
    ("https://page.kakao.com/landing/ranking/11/123?ranking_type=monthly",  "BL 웹소설 월간 TOP 300"),
    ("https://page.kakao.com/landing/series/poster/page/landing/4336",      "1억뷰+ 웹소설"),
]

GENRE_MAP = {
    "PURE": "로맨스",
    "FANTASY": "판타지",
    "DAILY": "일상",
    "로판": "로판",
    "HISTORICAL": "무협",
}

# ---- 리디북스 설정 ----
RIDIBOOKS_COOKIE_FILE = SESSIONS_DIR / "ridibooks_cookies.pkl"
LEZHIN_COOKIE_FILE = SESSIONS_DIR / "lezhin_cookies.pkl"
MRBLUE_COOKIE_FILE = SESSIONS_DIR / "mrblue_cookies.pkl"
RIDIBOOKS_LOGIN_URL = "https://ridibooks.com/account/login"

# (base_url, 추가 파라미터, 레이블, 최대 수집 수, 장르 힌트, 작품 유형)
RIDIBOOKS_INITIAL_TARGETS = [
    ("https://ridibooks.com/category/bestsellers/1612", "adult_exclude=y&period=steady",  "웹툰 로판 스테디 성인제외",   200, "로판",      "웹툰"),
    ("https://ridibooks.com/category/bestsellers/1612", "adult_include=y&period=steady",  "웹툰 로판 스테디 성인포함",   154, "로판",      "웹툰"),
    ("https://ridibooks.com/category/bestsellers/1613", "adult_exclude=y&period=steady",  "웹툰 로맨스 스테디 성인제외", 200, "로맨스",    "웹툰"),
    ("https://ridibooks.com/category/bestsellers/1613", "adult_include=y&period=steady",  "웹툰 로맨스 스테디 성인포함", 200, "로맨스",    "웹툰"),
    ("https://ridibooks.com/category/bestsellers/1606", "period=steady",                  "웹툰 판타지/SF 스테디",       200, "판타지",    "웹툰"),
    ("https://ridibooks.com/category/bestsellers/4250", "adult_include=y&period=steady",  "웹툰 BL 스테디 성인포함",    200, "BL",       "웹툰"),
    ("https://ridibooks.com/category/bestsellers/4250", "adult_exclude=y&period=steady",  "웹툰 BL 스테디 성인제외",    200, "BL",       "웹툰"),
    ("https://ridibooks.com/category/bestsellers/4250", "adult_include=y&period=monthly", "웹툰 BL 월간 성인포함",      200, "BL",       "웹툰"),
    ("https://ridibooks.com/category/bestsellers/4250", "adult_exclude=y&period=monthly", "웹툰 BL 월간 성인제외",      200, "BL",       "웹툰"),
]

# (base_url, 추가 파라미터, 레이블, 최대 수집 수, 장르 힌트, 작품 유형)
# 신작: 웹툰 전체 신작 인기순 상위 35 (#160). 리디는 상세 70건 안팎이면 차단돼 목록을 작게 잡는다.
# (url, 쿼리, 이름, 최대, 고정 장르 — None 이면 상세에서 판정, 유형)
RIDIBOOKS_NEW_TARGETS = [
    ("https://ridibooks.com/category/new-releases/1600", "order=popular", "웹툰 전체 신작 인기순", 35, None, "웹툰"),
]


MYSQL_CONFIG = {
    'host': os.getenv("MYSQL_DATABASE_HOST", "127.0.0.1"),
    'port': int(os.getenv("MYSQL_DATABASE_PORT", "13306")),
    'user': os.getenv("MYSQL_DATABASE_USER", "root"),
    'password': os.getenv("MYSQL_DATABASE_PASSWORD", ""),
    'database': os.getenv("MYSQL_DATABASE_NAME", "storix"),
    'charset': 'utf8mb4',
}
