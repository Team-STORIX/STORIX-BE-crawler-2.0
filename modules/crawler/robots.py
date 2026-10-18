"""robots.txt 검사 (#131). 크롤러의 페이지 이동(driver.get) · HTTP 요청(http_get)이 모두 여기를 거친다.

막힌 경로면 RobotsDisallowed 를 던져 그 수집을 멈춘다. 일부러 남긴 경로는 EXCEPTIONS 에 사유와 함께 둔다.
브라우저가 화면 안에서 따로 부르는 요청(XHR)은 여기서 보지 못한다 — 새 플랫폼을 붙일 때는 그 API 경로도 직접 대조한다.

    python -m modules.crawler.robots <url> [...]     # 주소마다 허용 · 차단 · 근거 규칙을 보여준다
"""
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

# (호스트, 경로 앞부분, 사유). 막힌 경로를 쓰려면 여기에 이슈 번호와 함께 넣는다
EXCEPTIONS = (
    ('nid.naver.com', '/nidlogin.login', '사람이 직접 입력하는 로그인 화면'),
    ('accounts.kakao.com', '/login', '사람이 직접 입력하는 로그인 화면'),
    ('ridibooks.com', '/account/login', '사람이 직접 입력하는 로그인 화면'),
    ('www.lezhin.com', '/ko/login', '사람이 직접 입력하는 로그인 화면'),
    ('series.naver.com', '/search/', '노션 요청 · 링크 채우기 검색. 뺄지 결정 대기 (#131)'),
)


class RobotsDisallowed(Exception):
    """robots.txt 가 막은 경로. 삼키지 말고 올린다 (PASS_THROUGH)."""


_cache: dict[str, list[tuple[str, str]] | None] = {}


def _fetch(host: str, user_agent: str) -> list[tuple[str, str]] | None:
    """User-agent: * 그룹의 (allow|disallow, 경로) 목록. robots.txt 가 없으면 [], 못 읽으면 None."""
    req = urllib.request.Request(f'https://{host}/robots.txt',
                                 headers={'User-Agent': user_agent, 'Accept-Language': 'ko-KR,ko;q=0.9'})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return parse(resp.read().decode('utf-8', 'replace'))
    except urllib.error.HTTPError as e:
        return [] if e.code in (404, 410) else None
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def parse(text: str) -> list[tuple[str, str]]:
    rules, star, in_group, seen_rule = [], False, False, False
    for raw in text.splitlines():
        line = raw.split('#', 1)[0].strip()
        if ':' not in line:
            continue
        key, value = (x.strip() for x in line.split(':', 1))
        key = key.lower()
        if key == 'user-agent':
            # 규칙이 나온 뒤의 User-agent 는 새 그룹이다. 연달아 나온 User-agent 는 한 그룹
            if seen_rule or not in_group:
                star, seen_rule = False, False
            in_group = True
            star = star or value == '*'
        elif key in ('allow', 'disallow') and in_group:
            seen_rule = True
            if star and value:
                rules.append((key, value))
    return rules


def _match_len(pattern: str, path: str) -> int | None:
    anchored = pattern.endswith('$')
    body = pattern[:-1] if anchored else pattern
    regex = '^' + re.escape(body).replace(r'\*', '.*') + ('$' if anchored else '')
    return len(pattern) if re.match(regex, path) else None


def verdict(rules: list[tuple[str, str]], path: str) -> tuple[bool, str]:
    """구글 방식: 가장 길게 맞는 규칙이 이기고, 길이가 같으면 allow."""
    best = None
    for kind, pattern in rules:
        n = _match_len(pattern, path)
        if n is not None and (best is None or n > best[0] or (n == best[0] and kind == 'allow')):
            best = (n, kind, pattern)
    if best is None:
        return True, ''
    return best[1] == 'allow', f'{best[1].capitalize()}: {best[2]}'


def exception_for(url: str) -> str | None:
    u = urllib.parse.urlsplit(url)
    return next((why for host, prefix, why in EXCEPTIONS if u.hostname == host and u.path.startswith(prefix)), None)


def check(url: str, user_agent: str) -> None:
    """막힌 경로면 RobotsDisallowed. robots.txt 를 못 읽으면 막지 않고 한 번 알린다."""
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ('http', 'https') or not u.hostname or exception_for(url):
        return
    if u.hostname not in _cache:
        _cache[u.hostname] = _fetch(u.hostname, user_agent)
        if _cache[u.hostname] is None:
            print(f'⚠️  robots.txt 를 읽지 못했습니다: {u.hostname} — 검사 없이 진행')
    rules = _cache[u.hostname]
    if not rules:
        return
    path = u.path or '/'
    ok, rule = verdict(rules, path + (f'?{u.query}' if u.query else ''))
    if not ok:
        raise RobotsDisallowed(f'robots.txt 가 막은 경로 ({rule}): {url}')


if __name__ == '__main__':
    from modules.crawler.base_crawler import USER_AGENT
    for arg in sys.argv[1:]:
        why = exception_for(arg)
        try:
            check(arg, USER_AGENT)
            print(f'{"⚠️  예외" if why else "✅ 허용"}  {arg}{f"  — {why}" if why else ""}')
        except RobotsDisallowed as e:
            print(f'❌ 차단  {e}')
