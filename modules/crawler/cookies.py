"""로그인 쿠키 파일 읽기 · 쓰기 한 곳 (#122). 크롤러 로그인 · 쿠키 갱신(keepalive)이 같이 쓴다.

파일은 selenium get_cookies() 형식 목록의 pickle 이다 (sessions/<플랫폼>_cookies.pkl).
"""
import os
import pickle
from pathlib import Path


def read_cookies(path) -> list[dict]:
    with open(path, 'rb') as f:
        return pickle.load(f)


def write_cookies(path, cookies) -> None:
    """임시 파일에 쓰고 바꿔 넣는다 (쓰다 끊겨도 기존 파일이 남게). 로그인 정보라 나만 읽게 600."""
    path = Path(path)
    tmp = path.with_suffix('.tmp')
    with open(tmp, 'wb') as f:
        pickle.dump(list(cookies), f)
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def apply_cookies(driver, path, domain: str | None = None) -> None:
    """쿠키 파일을 브라우저에 넣는다. 지금 연 페이지의 도메인 것만 들어간다 (다른 도메인 쿠키는 건너뜀).
    만료 시각은 빼고 넣는다 — 지난 시각이면 브라우저가 버린다. 만료는 사이트가 로그인 화면으로 보내는지로 본다.
    domain 을 주면 그 도메인으로 바꿔 넣는다 (네이버: nid.naver.com 쿠키를 .naver.com 으로)."""
    for c in read_cookies(path):
        c.pop('expiry', None)
        if domain:
            c['domain'] = domain
        try:
            driver.add_cookie(c)
        except Exception:
            pass


def _base(domain: str) -> str:
    """'.nid.naver.com' → 'naver.com'"""
    return '.'.join(domain.lstrip('.').split('.')[-2:])


def _selenium_cookie(c: dict) -> dict:
    """CDP Network.getAllCookies 한 개 → 쿠키 파일에 넣는 selenium 형식."""
    out = {'name': c['name'], 'value': c['value'], 'domain': c['domain'], 'path': c.get('path', '/'),
           'secure': c.get('secure', False), 'httpOnly': c.get('httpOnly', False)}
    if c.get('sameSite'):
        out['sameSite'] = c['sameSite']
    if not c.get('session') and c.get('expires', -1) > 0:
        out['expiry'] = int(c['expires'])
    return out


def browser_cookies(driver) -> list[dict]:
    """브라우저의 모든 도메인 쿠키 (get_cookies 는 지금 페이지 도메인 것만 준다)."""
    return [_selenium_cookie(c) for c in driver.execute_cdp_cmd('Network.getAllCookies', {})['cookies']]


def merge_cookies(old: list[dict], fresh: list[dict]) -> list[dict]:
    """기존 쿠키에 브라우저의 새 쿠키를 이름으로 덮어 쓴다. 기존 파일에 있던 도메인 것만 받는다 (광고 · 분석 쿠키 제외)."""
    bases = {_base(c.get('domain', '')) for c in old}
    merged = {c['name']: c for c in old}
    for c in fresh:
        if _base(c.get('domain', '')) in bases:
            merged[c['name']] = c
    return list(merged.values())
