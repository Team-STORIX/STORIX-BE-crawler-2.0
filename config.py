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

# 디렉토리 및 파일 경로
BASE_DIR = pathlib.Path(__file__).parent
SESSIONS_DIR = BASE_DIR / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
NAVER_COOKIE_FILE = SESSIONS_DIR / "naver_cookies.pkl"
KAKAO_COOKIE_FILE = SESSIONS_DIR / "kakao_cookies.pkl"
FAILED_CSV = BASE_DIR / "failed_rows.csv"
OUTPUT_CSV = BASE_DIR / "output_rows.csv"
OUTPUT_DIR = BASE_DIR / "output"

# ---- 네이버 웹툰 설정 ----

# 1차 크롤링 URL 및 장르 목록
BASE_URL = 'https://comic.naver.com/webtoon?tab=genre&genre='
GENRES = ["PURE", "FANTASY", "DAILY", "로판", "HISTORICAL"]

# 2차 크롤링 URL (daily - 인기순)
DAILY_PLUS_URL = 'https://comic.naver.com/webtoon?tab=dailyPlus'

# 2차 크롤링 URL + 수집 개수 제한 (completed - 완결)
COMPLETED_URL = 'https://comic.naver.com/webtoon?tab=finish'

# 신작 URL
NAVER_NEW_URL = 'https://comic.naver.com/webtoon?tab=new'

# ----------------------

# ---- 카카오 웹툰 설정 ----
KAKAO_LOGIN_URL = "https://accounts.kakao.com/login/?continue=https%3A%2F%2Fkauth.kakao.com%2Foauth%2Fauthorize%3Fclient_id%3D49bbb48c5fdb0199e5da1b89de359484%26state%3Dhttps%25253A%25252F%25252Fpage.kakao.com%25252Fmenu%25252F10010%25252Fscreen%25252F93%26redirect_uri%3Dhttps%253A%252F%252Fpage.kakao.com%252Frelay%252Flogin%26response_type%3Dcode%26auth_tran_id%3DW3lvNUKSoQz6HLrxqft_Qn0McwWmXpOWQ7Zo.f_58sE5Hx7anOVDmu5vgoIS%26ka%3Dsdk%252F2.1.0%2520os%252Fjavascript%2520sdk_type%252Fjavascript%2520lang%252Fko-KR%2520device%252FMacIntel%2520origin%252Fhttps%25253A%25252F%25252Fpage.kakao.com%26is_popup%3Dfalse%26through_account%3Dtrue&talk_login=hidden#login"
TOP_300_URL = "https://page.kakao.com/menu/10010/screen/93"
KAKAO_NEW_URL = "https://page.kakao.com/menu/10010/screen/94"

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
    "HISTORICAL": "무협/사극",
}

# ---- 리디북스 설정 ----
RIDIBOOKS_COOKIE_FILE = SESSIONS_DIR / "ridibooks_cookies.pkl"
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

# ---- 리디북스 new_works 대상 ----
# (base_url, 추가 파라미터, 레이블, 최대 수집 수, 장르 힌트, 작품 유형)
RIDIBOOKS_NEW_TARGETS = [
    ("https://ridibooks.com/category/bestsellers/1612", "adult_exclude=y&period=monthly", "웹툰 로판 월간 성인제외",    100, "로판",   "웹툰"),
    ("https://ridibooks.com/category/bestsellers/1613", "adult_exclude=y&period=monthly", "웹툰 로맨스 월간 성인제외", 100, "로맨스", "웹툰"),
    ("https://ridibooks.com/category/bestsellers/1606", "period=monthly",                  "웹툰 판타지 월간",          100, "판타지", "웹툰"),
    ("https://ridibooks.com/category/bestsellers/4250", "adult_exclude=y&period=monthly", "웹툰 BL 월간 성인제외",     100, "BL",    "웹툰"),
]

# 로그인 자격증명
NAVER_ID = os.getenv("NAVER_ID", "")
NAVER_PW = os.getenv("NAVER_PW", "")
KAKAO_ID = os.getenv("KAKAO_ID", "")
KAKAO_PW = os.getenv("KAKAO_PW", "")
RIDIBOOKS_ID = os.getenv("RIDIBOOKS_ID", "")
RIDIBOOKS_PW = os.getenv("RIDIBOOKS_PW", "")

# 데이터베이스 설정
MYSQL_CONFIG = {
    'host': os.getenv("MYSQL_DATABASE_HOST", "127.0.0.1"),
    'port': int(os.getenv("MYSQL_DATABASE_PORT", "13306")),
    'user': os.getenv("MYSQL_DATABASE_USER", "root"),
    'password': os.getenv("MYSQL_DATABASE_PASSWORD", ""),
    'database': os.getenv("MYSQL_DATABASE_NAME", "storix"),
    'charset': 'utf8mb4',
}
