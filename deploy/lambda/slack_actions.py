"""크롤러 슬랙 앱 버튼 처리 Lambda (#77). 슬랙 앱 Interactivity Request URL = 이 함수의 Function URL.

[📱 <플랫폼> 다시 로그인] 버튼 → 크롤러 서버에 로그인 모드 태그를 달고 켠다.
서버가 켜지면 deploy/nightly.sh 가 태그를 보고 수집 대신 crawler/remote_login.py 를 돌린다.
만료 알림은 야간 실행 중에 오므로 서버가 켜져 있으면 crawler:mode=login-after 만 단다 — 수집이 끝나면 끄기 전에 로그인한다.
[🔄 못 긁은 작품 다시 긁기] 버튼(다시 로그인 성공 답글) → 같은 방식으로 crawler:mode=rerun(-after). 노션 요청 처리만 다시 돈다.

보안
  - 슬랙 서명(X-Slack-Signature, Signing Secret) 검사 + 5분 지난 요청 거절 (재전송 막기)
  - 누른 사람이 SLACK_ALLOWED_USER_IDS 에 있어야 한다
  - 켜지는 중 · 꺼지는 중이면 아무것도 하지 않는다 (켜지는 중에 태그를 바꾸면 부팅 판단이 달라진다)
  - 이 함수의 권한은 크롤러 서버 한 대에 태그 달기 · 켜기 · 상태 보기뿐이다

환경변수: INSTANCE_ID, PARAM_PATH(/storix/crawler/)
"""
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request

import boto3

ssm = boto3.client('ssm')
ec2 = boto3.client('ec2')

INSTANCE_ID = os.environ['INSTANCE_ID']
PARAM_PATH = os.environ.get('PARAM_PATH', '/storix/crawler/')
RELOGIN, RERUN = 'relogin:', 'rerun:'
_params: dict = {}


def _param(name: str) -> str:
    if name not in _params:
        _params[name] = ssm.get_parameter(Name=PARAM_PATH + name, WithDecryption=True)['Parameter']['Value']
    return _params[name]


def verify(headers: dict, body: str, now: float | None = None) -> bool:
    ts = headers.get('x-slack-request-timestamp', '')
    sig = headers.get('x-slack-signature', '')
    if not ts.isdigit() or abs((now or time.time()) - int(ts)) > 300:
        return False
    expected = 'v0=' + hmac.new(_param('SLACK_SIGNING_SECRET').encode(), f'v0:{ts}:{body}'.encode(),
                                hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def allowed(user_id: str) -> bool:
    return user_id in {u.strip() for u in _param('SLACK_ALLOWED_USER_IDS').split(',') if u.strip()}


def reply(response_url: str, text: str) -> None:
    """누른 사람에게만 보이는 답 (ephemeral). 원래 알림은 그대로 둔다."""
    req = urllib.request.Request(response_url, method='POST',
                                 data=json.dumps({'response_type': 'ephemeral', 'replace_original': False,
                                                  'text': text}).encode(),
                                 headers={'Content-Type': 'application/json'})
    urllib.request.urlopen(req, timeout=5).read()


def start(kind: str, platform: str, user: str, channel: str, thread: str) -> str:
    """kind: login(다시 로그인) · rerun(다시 긁기). 꺼져 있으면 태그 달고 켜고, 켜져 있으면 하던 일 뒤에 하도록 태그만 단다."""
    instance = ec2.describe_instances(InstanceIds=[INSTANCE_ID])['Reservations'][0]['Instances'][0]
    state = instance['State']['Name']
    if state == 'running':
        if any(t['Key'] == 'crawler:mode' for t in instance.get('Tags') or []):
            return '⏳ 이미 처리를 기다리는 버튼 요청이 있습니다. 앞 요청이 끝난 뒤 다시 눌러 주세요.'
        mode = f'{kind}-after'
    elif state == 'stopped':
        mode = kind
    else:
        return f'⏳ 크롤러 서버가 지금 `{state}` 상태입니다. 잠시 뒤 다시 눌러 주세요.'
    ec2.create_tags(Resources=[INSTANCE_ID], Tags=[
        {'Key': 'crawler:mode', 'Value': mode},
        {'Key': 'crawler:login-platform', 'Value': platform},
        {'Key': 'crawler:login-user', 'Value': user},
        {'Key': 'crawler:login-channel', 'Value': channel},
        {'Key': 'crawler:login-thread', 'Value': thread},
    ])
    if mode == 'login-after':
        return f'🕐 크롤러가 지금 실행 중입니다. 끝나면 이어서 *{platform}* 원격 로그인 링크를 DM 으로 보냅니다.'
    if mode == 'rerun-after':
        return '🕐 크롤러가 지금 실행 중입니다. 끝나면 이어서 노션 요청을 다시 긁고 이 스레드에 결과를 답니다.'
    ec2.start_instances(InstanceIds=[INSTANCE_ID])
    if mode == 'rerun':
        return '🚀 크롤러 서버를 켜는 중입니다. 노션 요청을 다시 긁고 이 스레드에 결과를 답니다.'
    return f'🚀 크롤러 서버를 켜는 중입니다. 1~2분 뒤 *{platform}* 원격 로그인 링크가 DM 으로 옵니다.'


def handler(event, _context):
    headers = {k.lower(): v for k, v in (event.get('headers') or {}).items()}
    body = event.get('body') or ''
    if event.get('isBase64Encoded'):
        body = base64.b64decode(body).decode()
    if not verify(headers, body):
        return {'statusCode': 401, 'body': 'invalid signature'}

    payload = json.loads(urllib.parse.parse_qs(body).get('payload', ['{}'])[0])
    user = (payload.get('user') or {}).get('id', '')
    action = next((a for a in payload.get('actions') or [] if a.get('action_id', '').startswith((RELOGIN, RERUN))), None)
    response_url = payload.get('response_url', '')
    if not action:
        return {'statusCode': 200, 'body': ''}
    if not allowed(user):
        reply(response_url, '🚫 이 버튼을 누를 수 있는 사람이 아닙니다.')
        return {'statusCode': 200, 'body': ''}

    message = payload.get('container') or {}
    kind = 'rerun' if action['action_id'].startswith(RERUN) else 'login'
    text = start(kind, action['value'], user, message.get('channel_id', ''),
                 message.get('thread_ts') or message.get('message_ts', ''))
    reply(response_url, text)
    print(json.dumps({'user': user, 'kind': kind, 'value': action['value'], 'result': text}, ensure_ascii=False))
    return {'statusCode': 200, 'body': ''}
