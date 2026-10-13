"""서버 실행 알림 (#77). 서버에서는 사람이 로그를 보지 않으니 세션 만료 · 실행 실패를 슬랙으로 알린다.

크롤러 전용 슬랙 앱의 봇 토큰 · 알림 채널(SLACK_BOT_TOKEN · SLACK_CHANNEL_ID)로 chat.postMessage 를 부른다.
세션 만료 알림에는 플랫폼마다 [다시 로그인] 버튼을 단다 — 누르면 Lambda 가 서버를 원격 로그인 모드로 켠다 (remote_login).
둘 중 하나라도 없으면 보내지 않는다 — 로컬 실행은 알리지 않는다.
알림이 실패해도 실행 결과는 바꾸지 않는다 (출력만 남긴다).
"""
import json
import os
import urllib.request

from crawler import report

SLACK_URL = 'https://slack.com/api/chat.postMessage'

# 세션이 만료되면 할 일. 서버는 화면이 없어 로그인을 못 한다
RELOGIN_HINT = ('아래 버튼을 누르면 서버가 켜지고 폰으로 원격 로그인 링크가 DM 으로 옵니다. '
                '맥에서는 `python cli.py login --platform <플랫폼>` 뒤 `deploy/push_sessions.sh <쿠키 이름>` 도 됩니다.')
RELOGIN_ACTION = 'relogin'


def _api(method: str, body: dict) -> dict | None:
    token = os.getenv('SLACK_BOT_TOKEN', '')
    if not token:
        return None
    req = urllib.request.Request(f'https://slack.com/api/{method}', method='POST',
                                 data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json; charset=utf-8',
                                          'Authorization': f'Bearer {token}'})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read() or b'{}')
    except Exception as e:  # 알림 실패가 실행을 깨지 않게
        print(f'⚠️  슬랙 {method} 실패: {e}')
        return None
    if not data.get('ok'):
        print(f"⚠️  슬랙 {method} 실패: {data.get('error')}")
        return None
    return data


def slack(text: str, blocks: list | None = None, channel: str | None = None, thread_ts: str | None = None) -> bool:
    channel = channel or os.getenv('SLACK_CHANNEL_ID', '')
    if not channel:
        return False
    body = {'channel': channel, 'text': text, **({'blocks': blocks} if blocks else {}),
            **({'thread_ts': thread_ts} if thread_ts else {})}
    return _api('chat.postMessage', body) is not None


def dm(user: str, text: str) -> bool:
    """누른 사람에게만 보내는 메시지 (원격 로그인 링크)."""
    opened = _api('conversations.open', {'users': user})
    channel = ((opened or {}).get('channel') or {}).get('id')
    return bool(channel) and _api('chat.postMessage', {'channel': channel, 'text': text}) is not None


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


def relogin_blocks(text: str, platforms: list[str]) -> list[dict]:
    """알림 본문 + 만료된 플랫폼마다 [다시 로그인] 버튼. 버튼은 크롤러 슬랙 앱의 Interactivity(Lambda)로 간다."""
    return [
        {'type': 'section', 'text': {'type': 'mrkdwn', 'text': text}},
        {'type': 'actions', 'elements': [
            {'type': 'button', 'text': {'type': 'plain_text', 'text': f'📱 {p} 다시 로그인'},
             'action_id': f'{RELOGIN_ACTION}:{p}', 'value': p, 'style': 'primary'}
            for p in platforms[:5]]},
    ]


def notify_report(mode: str) -> bool:
    text = report_message(mode)
    if not text:
        return False
    expired = sorted({f['platform'] for f in report.summary()['failures'] if f['status'] == report.AUTH_EXPIRED})
    return slack(text, blocks=relogin_blocks(text, expired) if expired else None)


def notify_error(mode: str, error: BaseException) -> bool:
    return slack(f'🚨 크롤러 `{mode}` 가 오류로 멈췄습니다\n• {type(error).__name__}: {str(error)[:500]}')
