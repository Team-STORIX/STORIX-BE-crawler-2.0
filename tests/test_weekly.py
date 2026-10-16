"""주간 크롤러 작품 추가 알림 (#77)."""
from datetime import date, datetime

import pytest

from crawler import weekly


def test_last_week_is_previous_monday_to_monday():
    assert weekly.last_week(date(2026, 10, 19)) == (date(2026, 10, 12), date(2026, 10, 19))   # 월요일
    assert weekly.last_week(date(2026, 10, 25)) == (date(2026, 10, 12), date(2026, 10, 19))   # 일요일


def test_week_label_follows_thursday_month():
    assert weekly.week_label(date(2026, 10, 5)) == '10월 2주차'    # 목 10/8
    assert weekly.week_label(date(2026, 9, 28)) == '10월 1주차'    # 목 10/1 — 9월에 걸쳐도 10월
    assert weekly.week_label(date(2026, 10, 26)) == '10월 5주차'   # 목 10/29
    assert weekly.week_label(date(2026, 11, 30)) == '12월 1주차'   # 목 12/3


def test_utc_bounds_shift_kst_midnight():
    assert weekly.utc_bounds(date(2026, 10, 12), date(2026, 10, 19)) == (
        datetime(2026, 10, 11, 15, 0), datetime(2026, 10, 18, 15, 0))


def test_headline_and_thread_chunks(monkeypatch):
    assert weekly.headline('10월 2주차', 83) == '*:bell: [10월 2주차] 크롤러 작품 추가 알림*\n\n&lt;83건 추가&gt;'
    monkeypatch.setattr(weekly, 'THREAD_CHUNK', 2)
    assert weekly.chunks([('카카오페이지', ['- [웹툰] a', '- [웹툰] b', '- [웹툰] c']), ('리디북스', ['- [웹소설] d'])]) == [
        '*카카오페이지* (3)\n- [웹툰] a\n- [웹툰] b', '*카카오페이지* (이어서)\n- [웹툰] c', '*리디북스* (1)\n- [웹소설] d']


def test_sections_group_by_platform_and_merge_types():
    """플랫폼별로 묶고, 같은 플랫폼에서 같은 이름이 웹툰 · 웹소설로 따로 추가됐으면 한 줄로 (#109)"""
    rows = [{'works_name': '넷카마 펀치!!!', 'works_type': '웹소설', 'platforms': 'RIDIBOOKS'},
            {'works_name': '블랙 스완', 'works_type': '웹소설', 'platforms': 'KAKAO_PAGE'},
            {'works_name': '블랙 스완', 'works_type': '웹툰', 'platforms': 'KAKAO_PAGE'},
            {'works_name': '야화첩', 'works_type': '웹툰', 'platforms': 'LEZHIN,NAVER_WEBTOON'},   # 두 곳에서 만듦
            {'works_name': '이상한 곳', 'works_type': None, 'platforms': None}]
    assert weekly.sections(rows) == [
        ('네이버 웹툰', ['- [웹툰] 야화첩']),
        ('카카오페이지', ['- [웹툰/웹소설] 블랙 스완']),
        ('리디북스', ['- [웹소설] 넷카마 펀치!!!']),
        ('레진코믹스', ['- [웹툰] 야화첩']),
        ('기타', ['- 이상한 곳'])]


class FakeStore:
    def __init__(self, rows):
        self.rows, self.asked = rows, None

    def created_works(self, start, end):
        self.asked = (start, end)
        return self.rows


def test_run_posts_headline_then_names_in_thread(monkeypatch):
    posted, replies = [], []
    monkeypatch.setattr(weekly.notify, 'post', lambda text: posted.append(text) or '111.222')
    monkeypatch.setattr(weekly.notify, 'slack', lambda text, thread_ts=None: replies.append((text, thread_ts)) or True)
    monkeypatch.setattr(weekly, 'THREAD_CHUNK', 1)
    store = FakeStore([{'works_name': '작품1', 'works_type': '웹툰', 'platforms': 'KAKAO_PAGE'},
                       {'works_name': '작품2', 'works_type': '웹소설', 'platforms': 'RIDIBOOKS'}])
    r = weekly.run(store, date(2026, 10, 19))
    assert store.asked == (datetime(2026, 10, 11, 15, 0), datetime(2026, 10, 18, 15, 0))
    assert r['week'] == '10월 3주차' and r['created'] == 2 and r['sent'] is True
    assert posted == ['*:bell: [10월 3주차] 크롤러 작품 추가 알림*\n\n&lt;2건 추가&gt;']   # 원글에는 안내만
    assert replies == [('*카카오페이지* (1)\n- [웹툰] 작품1', '111.222'),                # 목록은 스레드로, 플랫폼마다
                       ('*리디북스* (1)\n- [웹소설] 작품2', '111.222')]


def test_run_reports_not_sent_when_headline_fails(monkeypatch):
    monkeypatch.setattr(weekly.notify, 'post', lambda text: None)
    monkeypatch.setattr(weekly.notify, 'slack', lambda *a, **k: pytest.fail('원글이 없으면 답글도 안 단다'))
    assert weekly.run(FakeStore([{'works_name': 'a'}]), date(2026, 10, 19))['sent'] is False


def test_run_does_not_send_empty_week_or_without_flag(monkeypatch):
    sent = []
    monkeypatch.setattr(weekly.notify, 'post', lambda text: sent.append(text) or 'ts')
    assert weekly.run(FakeStore([]), date(2026, 10, 19))['sent'] is False
    assert weekly.run(FakeStore([{'works_name': 'a'}]), date(2026, 10, 19), send=False)['sent'] is False
    assert sent == []
