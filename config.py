import os
from dotenv import load_dotenv
import pathlib

load_dotenv()

# 디렉토리 및 파일 경로
BASE_DIR = pathlib.Path(__file__).parent
NAVER_COOKIE_FILE = BASE_DIR / "naver_cookies.pkl"
KAKAO_COOKIE_FILE = BASE_DIR / "kakao_cookies.pkl"
FAILED_CSV = BASE_DIR / "failed_rows.csv"
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

GENRE_MAP = {
    "PURE": "로맨스",
    "FANTASY": "판타지",
    "DAILY": "일상",
    "로판": "로판",
    "HISTORICAL": "무협/사극" 
}

# 데이터베이스 설정 
MYSQL_CONFIG = {
    'host': os.getenv("MYSQL_DATABASE_HOST", "127.0.0.1"),
    'port': int(os.getenv("MYSQL_DATABASE_PORT", "13306")),
    'user': os.getenv("MYSQL_DATABASE_USER", "root"),
    'password': os.getenv("MYSQL_DATABASE_PASSWORD", ""),
    'database': os.getenv("MYSQL_DATABASE_NAME", "storix"),
    'charset': 'utf8mb4',
}