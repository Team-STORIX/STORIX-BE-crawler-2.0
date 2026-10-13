"""세션 등록 툴 (기획/운영 배포용 — 단독 실행 파일).

사용 흐름:
    1. 실행하면 플랫폼을 고른다 (엔터 = 전부)
    2. 크롬 창이 뜨면 평소처럼 로그인한다 (2차인증 포함)
    3. 로그인이 감지되면 자동으로 서버에 업로드되고 창이 닫힌다

서버 주소/토큰은 exe 옆의 session_tool.config.json 에서 읽는다:
    {"api_url": "http://<서버>:8100", "token": "<SESSION_API_TOKEN>"}

config 파일이 없으면 업로드 대신 ./sessions/*.pkl 로 저장만 한다
(파일을 개발팀에 전달하면 됨).

빌드: tools/build_exe.ps1 참고. 의존성: selenium, requests
"""
import json
import pickle
import sys
import time
from pathlib import Path

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

LOGIN_TIMEOUT_S = 600  # 로그인 대기 최대 10분
POLL_INTERVAL_S = 2

# platform → (표시명, 로그인 URL, 로그인 완료 판정 쿠키, 로그인 후 이동 URL, pkl 파일명)
# 판정 쿠키는 서버(api/session_api.py)의 marker와 반드시 일치해야 한다.
PLATFORMS = {
    'naver_webtoon': (
        '네이버',
        'https://nid.naver.com/nidlogin.login?mode=form&url=https://www.naver.com',
        'NID_AUT',
        'https://www.naver.com',
        'naver_cookies.pkl',
    ),
    'kakao_page': (
        '카카오',
        'https://accounts.kakao.com/login/?continue=https%3A%2F%2Fpage.kakao.com%2F',
        '_kawlt',
        'https://page.kakao.com/main',
        'kakao_cookies.pkl',
    ),
    'ridibooks': (
        '리디북스',
        'https://ridibooks.com/account/login',
        'ridi-at',
        'https://ridibooks.com',
        'ridibooks_cookies.pkl',
    ),
}


def _base_dir() -> Path:
    """exe로 빌드되면 exe 위치, 스크립트면 스크립트 위치."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _load_config() -> dict | None:
    path = _base_dir() / 'session_tool.config.json'
    if not path.exists():
        return None
    try:
        cfg = json.loads(path.read_text(encoding='utf-8'))
        if cfg.get('api_url') and cfg.get('token'):
            return cfg
    except Exception as e:
        print(f'⚠️  설정 파일을 읽지 못했습니다 ({e}). 로컬 저장 모드로 진행합니다.')
    return None


def _start_browser() -> webdriver.Chrome:
    options = Options()
    options.add_argument('--window-size=1200,900')
    options.add_argument('--lang=ko-KR')
    options.add_argument('--disable-extensions')
    options.add_experimental_option('excludeSwitches', ['enable-automation'])
    return webdriver.Chrome(options=options)


def _wait_for_login(driver: webdriver.Chrome, marker: str, label: str) -> bool:
    print(f'⏳ [{label}] 로그인을 기다리는 중입니다... (최대 {LOGIN_TIMEOUT_S // 60}분)')
    deadline = time.time() + LOGIN_TIMEOUT_S
    while time.time() < deadline:
        try:
            if driver.get_cookie(marker):
                return True
        except Exception:
            return False  # 사용자가 창을 닫음
        time.sleep(POLL_INTERVAL_S)
    return False


def _upload(cfg: dict, platform: str, cookies: list[dict], label: str) -> bool:
    url = cfg['api_url'].rstrip('/') + f'/sessions/{platform}'
    try:
        resp = requests.post(
            url,
            json=cookies,
            headers={'Authorization': f'Bearer {cfg["token"]}'},
            timeout=15,
        )
    except requests.RequestException as e:
        print(f'❌ [{label}] 서버 연결 실패: {e}')
        return False
    if resp.status_code == 200:
        return True
    try:
        detail = resp.json().get('detail', resp.text)
    except Exception:
        detail = resp.text
    print(f'❌ [{label}] 업로드 실패 ({resp.status_code}): {detail}')
    return False


def _save_local(platform: str, cookies: list[dict], pkl_name: str, label: str) -> Path:
    out_dir = _base_dir() / 'sessions'
    out_dir.mkdir(exist_ok=True)
    out = out_dir / pkl_name
    with open(out, 'wb') as f:
        pickle.dump(cookies, f)
    print(f'💾 [{label}] 쿠키를 파일로 저장했습니다: {out}')
    print('   이 파일을 개발팀에 전달해주세요.')
    return out


def register(platform: str, cfg: dict | None) -> bool:
    label, login_url, marker, post_login_url, pkl_name = PLATFORMS[platform]

    print(f'\n{"=" * 50}')
    print(f'🔐 [{label}] 세션 등록')
    print(f'{"=" * 50}')
    print('🔧 크롬 창을 엽니다. 뜨는 창에서 평소처럼 로그인해주세요. (2차인증 포함)')

    driver = _start_browser()
    try:
        driver.get(login_url)
        if not _wait_for_login(driver, marker, label):
            print(f'❌ [{label}] 로그인이 감지되지 않았습니다. 창을 닫고 다시 시도해주세요.')
            return False

        print(f'✅ [{label}] 로그인 감지! 쿠키를 수집합니다...')
        # 서비스 페이지로 이동해 해당 도메인 쿠키까지 전부 수집
        try:
            driver.get(post_login_url)
            time.sleep(2)
        except Exception:
            pass
        cookies = driver.get_cookies()
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    if not cookies:
        print(f'❌ [{label}] 쿠키를 가져오지 못했습니다.')
        return False

    if cfg:
        if _upload(cfg, platform, cookies, label):
            print(f'✅ [{label}] 세션 등록 완료!')
            return True
        _save_local(platform, cookies, pkl_name, label)
        return False

    _save_local(platform, cookies, pkl_name, label)
    return True


def main():
    print('STORIX 세션 등록 툴')
    print('-' * 50)

    cfg = _load_config()
    if cfg:
        print(f'🌐 업로드 서버: {cfg["api_url"]}')
    else:
        print('ℹ️  설정 파일(session_tool.config.json)이 없어 파일 저장 모드로 동작합니다.')

    keys = list(PLATFORMS)
    for i, k in enumerate(keys, 1):
        print(f'  {i}. {PLATFORMS[k][0]}')
    choice = input(f'\n등록할 플랫폼 번호를 입력하세요 (엔터 = 전부): ').strip()

    if not choice:
        targets = keys
    elif choice.isdigit() and 1 <= int(choice) <= len(keys):
        targets = [keys[int(choice) - 1]]
    else:
        print('❌ 잘못된 입력입니다.')
        input('창을 닫으려면 엔터를 누르세요...')
        return

    results = {p: register(p, cfg) for p in targets}

    print(f'\n{"=" * 50}')
    ok = [PLATFORMS[p][0] for p, r in results.items() if r]
    fail = [PLATFORMS[p][0] for p, r in results.items() if not r]
    if ok:
        print(f'✅ 등록 완료: {", ".join(ok)}')
    if fail:
        print(f'❌ 실패: {", ".join(fail)} — 다시 실행해주세요.')
        input('창을 닫으려면 엔터를 누르세요...')
        return

    print('5초 후 자동으로 닫힙니다.')
    time.sleep(5)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f'\n❌ 예상치 못한 오류: {e}')
        input('창을 닫으려면 엔터를 누르세요...')
