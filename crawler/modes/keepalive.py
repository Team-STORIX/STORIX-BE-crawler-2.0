"""keepalive 모드: 로그인 쿠키를 매일 한 번 열어 갱신한다.

    python cli.py keepalive                  # 쿠키 파일이 있는 플랫폼 전부
    python cli.py keepalive --platform naver_webtoon

쿠키로만 로그인해 보고
  - 열리면 → 사이트가 그동안 새로 내려준 쿠키를 기존 파일에 합쳐 저장한다. 쓸 때마다 연장되는 세션이 끊기지 않는다
  - 안 열리면 → AUTH_EXPIRED 로 남긴다. 그날 요청이 없는 플랫폼도 만료를 미리 알 수 있다 ([다시 로그인] 알림)
쿠키로 로그인하는 길(login_with_cookies)은 저장하지 않아, 이걸 따로 돌지 않으면 갱신된 쿠키가 브라우저와 함께 버려진다.
S3 에 올리는 건 nightly.sh 가 한다.
"""
from pathlib import Path

from crawler import report
from modules.crawler import platforms
from modules.crawler.cookies import browser_cookies, merge_cookies, read_cookies, write_cookies


def save_cookies(driver, path: Path) -> int:
    """브라우저 쿠키를 기존 파일에 합쳐 저장한다. 바뀐 쿠키 수를 돌려준다."""
    old = read_cookies(path)
    merged = merge_cookies(old, browser_cookies(driver))
    before = {c['name']: c.get('value') for c in old}
    write_cookies(path, merged)
    return sum(1 for c in merged if before.get(c['name']) != c.get('value'))


def refresh(name: str) -> bool | None:
    """쿠키 파일이 없으면 None, 로그인되면 저장하고 True, 만료면 False."""
    p = platforms.get(platforms.get(name).login)
    path = platforms.cookie_file(p.key)
    if not path.exists():
        print(f'⏭️  [{p.label}] 쿠키 파일 없음')
        return None
    with platforms.open_crawler(p.key, headless=True, login=None) as crawler:
        if not crawler.login_with_cookies():
            print(f'❌ [{p.label}] 쿠키로 로그인 안 됨 (만료)')
            report.record(p.key, report.AUTH_EXPIRED, '쿠키 갱신', '쿠키로 로그인 안 됨')
            return False
        changed = save_cookies(crawler.driver, path)
    print(f'🍪 [{p.label}] 쿠키 갱신 (바뀐 쿠키 {changed}개)')
    report.record(p.key, report.SUCCESS)
    return True


def run_keepalive(names: list[str] | None = None) -> dict:
    keys = list(dict.fromkeys(platforms.get(n).login for n in names)) if names else list(platforms.COOKIE_FILES)
    out = {}
    for key in keys:
        try:
            out[key] = refresh(key)
        except Exception as e:  # 한 플랫폼이 깨져도 나머지는 갱신한다
            print(f'⚠️  [{key}] 쿠키 갱신 실패: {type(e).__name__}: {e}')
            out[key] = None
    return out
