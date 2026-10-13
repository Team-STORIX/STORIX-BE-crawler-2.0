"""주간 크롤러 작품 추가 알림 (#77)."""
from datetime import date, datetime

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


def test_message_format_and_cap(monkeypatch):
    assert weekly.message('10월 2주차', ['가', '나']) == ':bell: [10월 2주차] 크롤러 작품 추가 알림\n\n&lt;2건 추가&gt;\n가\n나'
    monkeypatch.setattr(weekly, 'MAX_LINES', 2)
    assert weekly.message('x', ['a', 'b', 'c']).endswith('&lt;3건 추가&gt;\na\nb\n… 외 1건')


class FakeStore:
    def __init__(self, rows):
        self.rows, self.asked = rows, None

    def created_works(self, start, end):
        self.asked = (start, end)
        return self.rows


def test_run_sends_last_week(monkeypatch):
    sent = []
    monkeypatch.setattr(weekly.notify, 'slack', lambda text: sent.append(text) or True)
    store = FakeStore([{'works_name': '작품1'}, {'works_name': '작품2'}])
    r = weekly.run(store, date(2026, 10, 19))
    assert store.asked == (datetime(2026, 10, 11, 15, 0), datetime(2026, 10, 18, 15, 0))
    assert r['week'] == '10월 3주차' and r['created'] == 2 and r['sent'] is True
    assert sent == [':bell: [10월 3주차] 크롤러 작품 추가 알림\n\n&lt;2건 추가&gt;\n작품1\n작품2']


def test_run_does_not_send_empty_week_or_without_flag(monkeypatch):
    sent = []
    monkeypatch.setattr(weekly.notify, 'slack', lambda text: sent.append(text) or True)
    assert weekly.run(FakeStore([]), date(2026, 10, 19))['sent'] is False
    assert weekly.run(FakeStore([{'works_name': 'a'}]), date(2026, 10, 19), send=False)['sent'] is False
    assert sent == []
