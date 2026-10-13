"""서버 실행 알림 (#77). 서버에서는 사람이 로그를 보지 않으니 세션 만료 · 실행 실패를 슬랙으로 알린다.

BE 와 같은 봇 토큰 · 채널(SLACK_BOT_TOKEN · SLACK_CHANNEL_ID)로 chat.postMessage 를 부른다.
둘 중 하나라도 없으면 보내지 않는다 — 로컬 실행은 알리지 않는다.
알림이 실패해도 실행 결과는 바꾸지 않는다 (출력만 남긴다).
"""
import json
import os
import urllib.request

from crawler import report

SLACK_URL = 'https://slack.com/api/chat.postMessage'

# 세션이 만료되면 할 일. 서버는 화면이 없어 로그인을 못 한다
RELOGIN_HINT = ('로컬에서 `python cli.py login --platform <플랫폼>` 로 다시 로그인하고 '
                '`deploy/push_sessions.sh <쿠키 이름>` 으로 올려 주세요. 다음 자정 실행부터 씁니다.')


def slack(text: str) -> bool:
    token, channel = os.getenv('SLACK_BOT_TOKEN', ''), os.getenv('SLACK_CHANNEL_ID', '')
    if not token or not channel:
        return False
    req = urllib.request.Request(SLACK_URL, method='POST',
                                 data=json.dumps({'channel': channel, 'text': text}).encode(),
                                 headers={'Content-Type': 'application/json; charset=utf-8',
                                          'Authorization': f'Bearer {token}'})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = json.loads(r.read() or b'{}')
    except Exception as e:  # 알림 실패가 실행을 깨지 않게
        print(f'⚠️  슬랙 알림 실패: {e}')
        return False
    if not body.get('ok'):
        print(f"⚠️  슬랙 알림 실패: {body.get('error')}")
        return False
    return True


def report_message(mode: str, data: dict | None = None) -> str | None:
    """수집 리포트에서 알릴 내용을 만든다. 런 실패(세션 만료 · 차단 · 수집 0건)가 없으면 None."""
    data = data or report.summary()
    fatal = [f for f in data['failures'] if f['status'] in report.FATAL]
    if not fatal:
        return None
    lines = [f'🚨 크롤러 `{mode}` 실패']
    expired = sorted({f['platform'] for f in fatal if f['status'] == report.AUTH_EXPIRED})
    if expired:
        lines.append(f"• 세션 만료: {', '.join(expired)}")
        lines.append(f'  {RELOGIN_HINT}')
    for status in (report.RATE_LIMITED, report.PARSING_FAILED):
        platforms = sorted({f['platform'] for f in fatal if f['status'] == status})
        if platforms:
            lines.append(f"• {status}: {', '.join(platforms)}")
    for f in [f for f in fatal if f['status'] != report.AUTH_EXPIRED][:5]:
        lines.append(f"  - {f['platform']} {f['target']} {f['detail']}".rstrip())
    return '\n'.join(lines)


def notify_report(mode: str) -> bool:
    text = report_message(mode)
    return slack(text) if text else False


def notify_error(mode: str, error: BaseException) -> bool:
    return slack(f'🚨 크롤러 `{mode}` 가 오류로 멈췄습니다\n• {type(error).__name__}: {str(error)[:500]}')
