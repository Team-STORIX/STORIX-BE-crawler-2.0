"""크롤러 슬랙 버튼 Lambda (#77). boto3 없이 돈다 (가짜 클라이언트)."""
import hashlib
import hmac
import importlib
import json
import sys
import time
import types
import urllib.parse
from pathlib import Path

import pytest

SECRET = 'signing-secret'


class FakeSSM:
    values = {'/storix/crawler/SLACK_SIGNING_SECRET': SECRET,
              '/storix/crawler/SLACK_ALLOWED_USER_IDS': 'U1, U2'}

    def get_parameter(self, Name, WithDecryption):
        return {'Parameter': {'Value': self.values[Name]}}


class FakeEC2:
    def __init__(self):
        self.state, self.tags, self.started = 'stopped', [], False

    def describe_instances(self, InstanceIds):
        return {'Reservations': [{'Instances': [{'State': {'Name': self.state}}]}]}

    def create_tags(self, Resources, Tags):
        self.tags = Tags

    def start_instances(self, InstanceIds):
        self.started = True


@pytest.fixture
def mod(monkeypatch):
    ec2 = FakeEC2()
    fake_boto3 = types.SimpleNamespace(client=lambda name: FakeSSM() if name == 'ssm' else ec2)
    monkeypatch.setitem(sys.modules, 'boto3', fake_boto3)
    monkeypatch.setenv('INSTANCE_ID', 'i-test')
    monkeypatch.syspath_prepend(str(Path(__file__).parent.parent / 'deploy' / 'lambda'))
    sys.modules.pop('slack_actions', None)
    m = importlib.import_module('slack_actions')
    replies = []
    monkeypatch.setattr(m, 'reply', lambda url, text: replies.append(text))
    m.fake_ec2, m.replies = ec2, replies
    return m


def event(payload: dict, ts: int | None = None, secret: str = SECRET) -> dict:
    body = urllib.parse.urlencode({'payload': json.dumps(payload)})
    ts = str(ts or int(time.time()))
    sig = 'v0=' + hmac.new(secret.encode(), f'v0:{ts}:{body}'.encode(), hashlib.sha256).hexdigest()
    return {'headers': {'X-Slack-Request-Timestamp': ts, 'X-Slack-Signature': sig}, 'body': body}


def click(user='U1', platform='kakao_page'):
    return {'user': {'id': user}, 'response_url': 'https://hooks.slack.com/x',
            'actions': [{'action_id': f'relogin:{platform}', 'value': platform}],
            'container': {'channel_id': 'C1', 'message_ts': '111.222'}}


def test_rejects_bad_or_old_signature(mod):
    assert mod.handler(event(click(), secret='wrong'), None)['statusCode'] == 401
    assert mod.handler(event(click(), ts=int(time.time()) - 600), None)['statusCode'] == 401
    assert not mod.fake_ec2.started


def test_rejects_user_not_allowed(mod):
    assert mod.handler(event(click(user='U9')), None)['statusCode'] == 200
    assert not mod.fake_ec2.started
    assert '누를 수 있는 사람이 아닙니다' in mod.replies[0]


def test_tags_and_starts_server(mod):
    mod.handler(event(click(user='U2', platform='naver_series')), None)
    assert mod.fake_ec2.started
    assert {t['Key']: t['Value'] for t in mod.fake_ec2.tags} == {
        'crawler:mode': 'login', 'crawler:login-platform': 'naver_series', 'crawler:login-user': 'U2',
        'crawler:login-channel': 'C1', 'crawler:login-thread': '111.222'}
    assert '켜는 중' in mod.replies[0]


def test_does_not_start_when_already_running(mod):
    mod.fake_ec2.state = 'running'
    mod.handler(event(click()), None)
    assert not mod.fake_ec2.started and mod.fake_ec2.tags == []
    assert 'running' in mod.replies[0]
