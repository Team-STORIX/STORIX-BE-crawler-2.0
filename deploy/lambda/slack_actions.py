"""크롤러 슬랙 앱 버튼 처리 Lambda (#77). 슬랙 앱 Interactivity Request URL = 이 함수의 Function URL.

[📱 <플랫폼> 다시 로그인] 버튼 → 크롤러 서버에 로그인 모드 태그를 달고 켠다.
서버가 켜지면 deploy/nightly.sh 가 태그를 보고 수집 대신 crawler/remote_login.py 를 돌린다.

보안
  - 슬랙 서명(X-Slack-Signature, Signing Secret) 검사 + 5분 지난 요청 거절 (재전송 막기)
  - 누른 사람이 SLACK_ALLOWED_USER_IDS 에 있어야 한다
  - 서버가 이미 켜져 있으면(야간 실행 · 다른 로그인 중) 켜지 않는다
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
RELOGIN = 'relogin:'
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


def start_login(platform: str, user: str, channel: str, thread: str) -> str:
    state = ec2.describe_instances(InstanceIds=[INSTANCE_ID])['Reservations'][0]['Instances'][0]['State']['Name']
    if state != 'stopped':
        return f'⏳ 크롤러 서버가 지금 `{state}` 상태입니다. 꺼진 뒤 다시 눌러 주세요.'
    ec2.create_tags(Resources=[INSTANCE_ID], Tags=[
        {'Key': 'crawler:mode', 'Value': 'login'},
        {'Key': 'crawler:login-platform', 'Value': platform},
        {'Key': 'crawler:login-user', 'Value': user},
        {'Key': 'crawler:login-channel', 'Value': channel},
        {'Key': 'crawler:login-thread', 'Value': thread},
    ])
    ec2.start_instances(InstanceIds=[INSTANCE_ID])
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
    action = next((a for a in payload.get('actions') or [] if a.get('action_id', '').startswith(RELOGIN)), None)
    response_url = payload.get('response_url', '')
    if not action:
        return {'statusCode': 200, 'body': ''}
    if not allowed(user):
        reply(response_url, '🚫 이 버튼을 누를 수 있는 사람이 아닙니다.')
        return {'statusCode': 200, 'body': ''}

    message = payload.get('container') or {}
    text = start_login(action['value'], user, message.get('channel_id', ''),
                       message.get('thread_ts') or message.get('message_ts', ''))
    reply(response_url, text)
    print(json.dumps({'user': user, 'platform': action['value'], 'result': text}, ensure_ascii=False))
    return {'statusCode': 200, 'body': ''}
