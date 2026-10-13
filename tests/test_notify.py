"""서버 실행 알림 (#77)."""
import json

import pytest

from crawler import notify, report


@pytest.fixture(autouse=True)
def clean_report():
    report.reset()
    yield
    report.reset()


class FakeResponse:
    def __init__(self, body: dict):
        self.body = json.dumps(body).encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_no_message_without_fatal_failures():
    report.record('kakao_page', report.SUCCESS)
    report.record('kakao_page', report.SEARCH_FAILED, '없는 작품')
    assert notify.report_message('requests') is None


def test_session_expiry_message_names_platforms_and_relogin():
    report.record('kakao_page', report.AUTH_EXPIRED, '플랫폼 중단', 'AuthExpiredError: 쿠키 만료')
    report.record('naver_webtoon', report.AUTH_EXPIRED, '플랫폼 중단', 'AuthExpiredError: 쿠키 만료')
    report.record('ridibooks', report.RATE_LIMITED, '플랫폼 중단', 'RateLimitedError: 403')
    text = notify.report_message('requests')
    assert '세션 만료: kakao_page, naver_webtoon' in text
    assert 'cli.py login' in text
    assert 'RATE_LIMITED: ridibooks' in text
    assert 'RateLimitedError: 403' in text
    assert 'AuthExpiredError' not in text  # 세션 만료는 플랫폼 이름 · 할 일만 적는다


def test_slack_is_skipped_without_token(monkeypatch):
    monkeypatch.delenv('SLACK_BOT_TOKEN', raising=False)
    monkeypatch.delenv('SLACK_CHANNEL_ID', raising=False)
    called = []
    monkeypatch.setattr(notify.urllib.request, 'urlopen', lambda *a, **k: called.append(a))
    assert notify.slack('hi') is False
    assert called == []


def test_slack_posts_to_channel(monkeypatch):
    monkeypatch.setenv('SLACK_BOT_TOKEN', 'xoxb-test')
    monkeypatch.setenv('SLACK_CHANNEL_ID', 'C123')
    sent = []

    def urlopen(req, timeout):
        sent.append(req)
        return FakeResponse({'ok': True})

    monkeypatch.setattr(notify.urllib.request, 'urlopen', urlopen)
    report.record('kakao_page', report.AUTH_EXPIRED, '플랫폼 중단')
    assert notify.notify_report('requests') is True
    assert sent[0].full_url == notify.SLACK_URL
    assert sent[0].get_header('Authorization') == 'Bearer xoxb-test'
    body = json.loads(sent[0].data)
    assert body['channel'] == 'C123' and '세션 만료: kakao_page' in body['text']


def test_slack_failure_does_not_raise(monkeypatch):
    monkeypatch.setenv('SLACK_BOT_TOKEN', 'xoxb-test')
    monkeypatch.setenv('SLACK_CHANNEL_ID', 'C123')

    def boom(*a, **k):
        raise OSError('network down')

    monkeypatch.setattr(notify.urllib.request, 'urlopen', boom)
    assert notify.notify_error('requests', ValueError('x')) is False
    monkeypatch.setattr(notify.urllib.request, 'urlopen', lambda *a, **k: FakeResponse({'ok': False, 'error': 'not_in_channel'}))
    assert notify.slack('hi') is False
