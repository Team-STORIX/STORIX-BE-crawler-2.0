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


def test_session_expiry_message_template():
    report.record('kakao_page', report.AUTH_EXPIRED, '플랫폼 중단', 'AuthExpiredError: 쿠키 만료')
    assert notify.report_message('requests') == (
        '*:rotating_light: 크롤러 인증 만료*\n'
        '• 플랫폼: kakao_page\n'
        '\n'
        '아래 버튼을 누르면 서버가 켜지고 원격 로그인 링크가 DM 으로 옵니다.')


def test_session_expiry_with_other_failures():
    report.record('kakao_page', report.AUTH_EXPIRED, '플랫폼 중단', 'AuthExpiredError: 쿠키 만료')
    report.record('naver_webtoon', report.AUTH_EXPIRED, '플랫폼 중단', 'AuthExpiredError: 쿠키 만료')
    report.record('ridibooks', report.RATE_LIMITED, '플랫폼 중단', 'RateLimitedError: 403')
    text = notify.report_message('requests')
    assert text.startswith('*:rotating_light: 크롤러 인증 만료*\n• 플랫폼: kakao_page, naver_webtoon\n')
    assert '*:rotating_light: 크롤러 `requests` 실패*' in text
    assert 'RATE_LIMITED: ridibooks' in text and 'RateLimitedError: 403' in text
    assert 'AuthExpiredError' not in text  # 인증 만료는 플랫폼 · 버튼 안내만


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
    assert body['channel'] == 'C123' and '• 플랫폼: kakao_page' in body['text']


def test_slack_failure_does_not_raise(monkeypatch):
    monkeypatch.setenv('SLACK_BOT_TOKEN', 'xoxb-test')
    monkeypatch.setenv('SLACK_CHANNEL_ID', 'C123')

    def boom(*a, **k):
        raise OSError('network down')

    monkeypatch.setattr(notify.urllib.request, 'urlopen', boom)
    assert notify.notify_error('requests', ValueError('x')) is False
    monkeypatch.setattr(notify.urllib.request, 'urlopen', lambda *a, **k: FakeResponse({'ok': False, 'error': 'not_in_channel'}))
    assert notify.slack('hi') is False


def test_session_expiry_has_relogin_buttons(monkeypatch):
    monkeypatch.setenv('SLACK_BOT_TOKEN', 'xoxb-test')
    monkeypatch.setenv('SLACK_CHANNEL_ID', 'C123')
    sent = []
    monkeypatch.setattr(notify.urllib.request, 'urlopen',
                        lambda req, timeout: sent.append(json.loads(req.data)) or FakeResponse({'ok': True}))
    report.record('naver_series', report.AUTH_EXPIRED, '플랫폼 중단')
    report.record('kakao_page', report.AUTH_EXPIRED, '플랫폼 중단')
    report.record('ridibooks', report.RATE_LIMITED, '플랫폼 중단')   # 차단은 버튼 없음
    assert notify.notify_report('requests') is True
    buttons = sent[0]['blocks'][1]['elements']
    assert [(b['action_id'], b['value']) for b in buttons] == [
        ('relogin:kakao_page', 'kakao_page'), ('relogin:naver_series', 'naver_series')]


def test_no_buttons_without_session_expiry(monkeypatch):
    monkeypatch.setenv('SLACK_BOT_TOKEN', 'xoxb-test')
    monkeypatch.setenv('SLACK_CHANNEL_ID', 'C123')
    sent = []
    monkeypatch.setattr(notify.urllib.request, 'urlopen',
                        lambda req, timeout: sent.append(json.loads(req.data)) or FakeResponse({'ok': True}))
    report.record('ridibooks', report.RATE_LIMITED, '플랫폼 중단')
    notify.notify_report('requests')
    assert 'blocks' not in sent[0]


def test_dm_opens_conversation_then_posts(monkeypatch):
    monkeypatch.setenv('SLACK_BOT_TOKEN', 'xoxb-test')
    calls = []

    def urlopen(req, timeout):
        calls.append((req.full_url.rsplit('/', 1)[1], json.loads(req.data)))
        if req.full_url.endswith('conversations.open'):
            return FakeResponse({'ok': True, 'channel': {'id': 'D999'}})
        return FakeResponse({'ok': True})

    monkeypatch.setattr(notify.urllib.request, 'urlopen', urlopen)
    assert notify.dm('U1', 'link') is True
    assert calls == [('conversations.open', {'users': 'U1'}),
                     ('chat.postMessage', {'channel': 'D999', 'text': 'link'})]


def test_thread_reply_and_channel_override(monkeypatch):
    monkeypatch.setenv('SLACK_BOT_TOKEN', 'xoxb-test')
    monkeypatch.delenv('SLACK_CHANNEL_ID', raising=False)
    sent = []
    monkeypatch.setattr(notify.urllib.request, 'urlopen',
                        lambda req, timeout: sent.append(json.loads(req.data)) or FakeResponse({'ok': True}))
    assert notify.slack('done', channel='C9', thread_ts='1.2') is True
    assert sent == [{'channel': 'C9', 'text': 'done', 'thread_ts': '1.2'}]


def test_thread_reply_reacts_on_parent_with_same_emoji(monkeypatch):
    monkeypatch.setenv('SLACK_BOT_TOKEN', 'xoxb-test')
    calls = []
    monkeypatch.setattr(notify.urllib.request, 'urlopen', lambda req, timeout: calls.append(
        (req.full_url.rsplit('/', 1)[1], json.loads(req.data))) or FakeResponse({'ok': True}))
    assert notify.thread_reply('closed_lock_with_key', '링크 보냄', 'C1', '111.222') is True
    assert calls == [
        ('chat.postMessage', {'channel': 'C1', 'text': ':closed_lock_with_key: 링크 보냄', 'thread_ts': '111.222'}),
        ('reactions.add', {'channel': 'C1', 'timestamp': '111.222', 'name': 'closed_lock_with_key'})]


def test_post_returns_message_ts(monkeypatch):
    monkeypatch.setenv('SLACK_BOT_TOKEN', 'xoxb-test')
    monkeypatch.setenv('SLACK_CHANNEL_ID', 'C1')
    monkeypatch.setattr(notify.urllib.request, 'urlopen', lambda req, timeout: FakeResponse({'ok': True, 'ts': '123.456'}))
    assert notify.post('hi') == '123.456'
    monkeypatch.setattr(notify.urllib.request, 'urlopen', lambda req, timeout: FakeResponse({'ok': False, 'error': 'x'}))
    assert notify.post('hi') is None and notify.slack('hi') is False


def test_session_expiry_is_alerted_once_a_day(monkeypatch, tmp_path):
    """keepalive 가 알린 만료를 같은 밤 requests 가 또 알리지 않는다. 같은 쿠키(네이버)를 쓰는 플랫폼도 한 번."""
    monkeypatch.setenv('SLACK_BOT_TOKEN', 'xoxb-test')
    monkeypatch.setenv('SLACK_CHANNEL_ID', 'C123')
    monkeypatch.setattr(notify, '_alerted_file', lambda: tmp_path / 'alerted_expired.json')
    sent = []
    monkeypatch.setattr(notify.urllib.request, 'urlopen',
                        lambda req, timeout: sent.append(json.loads(req.data)) or FakeResponse({'ok': True, 'ts': '1.2'}))

    report.record('naver_webtoon', report.AUTH_EXPIRED, '쿠키 갱신', '쿠키로 로그인 안 됨')
    assert notify.notify_report('keepalive') is True

    report.reset()
    report.record('naver_series', report.AUTH_EXPIRED, '플랫폼 중단', 'AuthExpiredError')
    assert notify.notify_report('requests') is False

    report.record('kakao_page', report.AUTH_EXPIRED, '플랫폼 중단', 'AuthExpiredError')
    assert notify.notify_report('requests') is True
    assert 'kakao_page' in sent[-1]['text'] and 'naver' not in sent[-1]['text']
    assert len(sent) == 2


def test_host_command_posts_rerun_button_and_reacts(monkeypatch):
    """서버 호스트(nightly.sh)도 같은 모듈로 보낸다 — 다시 긁기 버튼 · 반응 (#122)."""
    monkeypatch.setenv('SLACK_BOT_TOKEN', 'xoxb-test')
    monkeypatch.setenv('SLACK_CHANNEL_ID', 'C123')
    sent = []
    monkeypatch.setattr(notify.urllib.request, 'urlopen',
                        lambda req, timeout: sent.append((req.full_url, json.loads(req.data))) or FakeResponse({'ok': True, 'ts': '1.2'}))
    assert notify.main(['post', '완료', '--thread', '9.9', '--rerun-button']) == 0
    url, body = sent[-1]
    assert url.endswith('chat.postMessage') and body['thread_ts'] == '9.9'
    assert body['blocks'][1]['elements'][0]['action_id'] == 'rerun:requests'
    assert notify.main(['react', 'white_check_mark', 'C1', '9.9']) == 0
    assert sent[-1][0].endswith('reactions.add') and sent[-1][1]['name'] == 'white_check_mark'
