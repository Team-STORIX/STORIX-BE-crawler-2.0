"""폰 원격 로그인 (#77). 세션 만료 알림의 [다시 로그인] 버튼 → Lambda 가 서버를 로그인 모드로 켜면 nightly.sh 가 부른다.

    python -m crawler.remote_login --platform kakao_page --user U123 --channel C123 --thread 1700000000.000100

서버 컨테이너 안에서:
  1) 가상 화면(Xvfb) · 원격 화면(x11vnc → noVNC) · Cloudflare 임시 터널(들어오는 포트 없이 https 주소)을 띄운다
  2) 원격 화면 링크를 누른 사람에게만 DM 으로 보낸다 (알림 스레드에는 "DM 보냄" 만)
  3) 그 화면에 기존 `cli.py login` 을 띄운다 — 사람이 폰으로 캡차 · 2차 인증 · OTP 를 넣는다
  4) 끝나면 모두 닫는다. 쿠키를 S3 에 올리고 결과를 알리는 건 nightly.sh 가 한다 (호스트에 S3 권한이 있다)

보안: 링크는 무작위 터널 주소 + 1회용 VNC 비밀번호, 한 번에 한 기기만 접속(-nevershared), 30분 뒤 전부 닫힘.
화면에는 로그인용 Chrome 창 하나만 뜬다 (데스크톱 · 터미널 없음).
"""
import argparse
import os
import re
import secrets
import subprocess
import sys
import time

from crawler import notify
from modules.crawler import platforms

DISPLAY = ':99'
SCREEN = '900x1600x24'          # 폰 세로 화면
WINDOW = '900,1600'
LIMIT_SEC = 30 * 60
VNC_PORT, WEB_PORT = 5900, 6080
TUNNEL_URL = re.compile(r'https://[a-z0-9-]+\.trycloudflare\.com')

def _start(cmd: list[str], **kw) -> subprocess.Popen:
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)


def _tunnel() -> tuple[subprocess.Popen, str]:
    """Cloudflare 임시 터널. 서버에서 밖으로 나가는 연결만 쓰므로 보안그룹을 열지 않는다."""
    proc = subprocess.Popen(['cloudflared', 'tunnel', '--no-autoupdate', '--url', f'http://localhost:{WEB_PORT}'],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    deadline = time.time() + 60
    while time.time() < deadline:
        line = proc.stderr.readline()
        if not line and proc.poll() is not None:
            break
        m = TUNNEL_URL.search(line or '')
        if m:
            return proc, m.group(0)
    proc.kill()
    raise RuntimeError('Cloudflare 임시 터널 주소를 못 받았습니다')


def run(platform: str, user: str, channel: str, thread: str | None) -> int:
    try:
        login_platform = platforms.get(platform).login   # 네이버 웹툰 · 시리즈 · 웹소설은 쿠키 하나
    except KeyError:
        notify.thread_reply('x', f'원격 로그인을 지원하지 않는 플랫폼: {platform}', channel, thread)
        return 2

    password = secrets.token_urlsafe(6)[:8]   # VNC 비밀번호는 8자까지
    procs: list[subprocess.Popen] = []
    try:
        procs.append(_start(['Xvfb', DISPLAY, '-screen', '0', SCREEN, '-nolisten', 'tcp']))
        time.sleep(1)
        procs.append(_start(['x11vnc', '-display', DISPLAY, '-rfbport', str(VNC_PORT), '-localhost',
                             '-passwd', password, '-nevershared', '-dontdisconnect', '-forever', '-quiet']))
        procs.append(_start(['websockify', '--web', '/usr/share/novnc', str(WEB_PORT), f'localhost:{VNC_PORT}']))
        tunnel, url = _tunnel()
        procs.append(tunnel)

        link = f'{url}/vnc.html?autoconnect=1&resize=scale&password={password}'
        if not notify.dm(user, f'📱 *{platform}* 원격 로그인 (30분 유효, 한 기기만 접속)\n{link}\n'
                               '열리는 Chrome 창에서 로그인 · 인증을 마치면 자동으로 감지합니다.'):
            raise RuntimeError('원격 로그인 링크 DM 을 못 보냈습니다')
        notify.thread_reply('closed_lock_with_key', f'<@{user}> 님께 *{platform}* 원격 로그인 링크를 DM 으로 보냈습니다 (30분 유효)',
                            channel, thread)

        # 기존 로그인 명령을 원격 화면에 띄운다. DOCKER_ENV 를 빼야 수동 로그인 화면을 연다
        env = {k: v for k, v in os.environ.items() if k != 'DOCKER_ENV'}
        env.update(DISPLAY=DISPLAY, CHROME_WINDOW_SIZE=WINDOW, LOGIN_WAIT_SEC=str(LIMIT_SEC - 120))
        try:
            done = subprocess.run([sys.executable, 'cli.py', 'login', '--platform', login_platform],
                                  env=env, timeout=LIMIT_SEC, capture_output=True, text=True)
            ok = done.returncode == 0 and '로그인 성공' in done.stdout
            print(done.stdout[-2000:])
        except subprocess.TimeoutExpired:
            ok = False
        if not ok:
            notify.thread_reply('x', f'*{platform}* 원격 로그인이 끝나지 않았습니다 (시간 초과 또는 실패). 다시 누르면 새로 시작합니다',
                                channel, thread)
        return 0 if ok else 1
    finally:
        for p in reversed(procs):
            p.kill()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--platform', required=True)
    p.add_argument('--user', required=True)
    p.add_argument('--channel', required=True)
    p.add_argument('--thread')
    a = p.parse_args()
    try:
        code = run(a.platform, a.user, a.channel, a.thread)
    except Exception as e:
        notify.thread_reply('x', f'*{a.platform}* 원격 로그인 준비 실패: {e}', a.channel, a.thread)
        code = 1
    sys.exit(code)


if __name__ == '__main__':
    main()
