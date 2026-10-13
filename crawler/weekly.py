"""주간 "크롤러 작품 추가" 알림 (#77). 월요일 자정 실행 때 지난주(월~일, 한국 시간)에 BE 에 새로 만든 작품을 보낸다.

원글은 안내만, 작품명은 그 원글의 스레드 답글로 (100개씩 나눠서):

    *:bell: [10월 2주차] 크롤러 작품 추가 알림*

    <N건 추가>
      └ 작품명1
        작품명2

새로 만든 작품 = works_staging.import_result 가 CREATED. 갱신(UPDATED · UNCHANGED)은 넣지 않는다.
노션 요청뿐 아니라 로컬 stage import 로 넣은 것도 같은 staging DB 를 거치므로 함께 잡힌다.
"""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from crawler import notify

KST = ZoneInfo('Asia/Seoul')
THREAD_CHUNK = 100  # 스레드 답글 하나에 넣는 작품 수


def last_week(today: date) -> tuple[date, date]:
    """today 가 속한 주의 직전 주 [월요일, 다음 월요일)."""
    monday = today - timedelta(days=today.weekday() + 7)
    return monday, monday + timedelta(days=7)


def week_label(monday: date) -> str:
    """주차는 그 주 목요일이 속한 달 기준. 10/5(월)~10/11(일) 주는 목요일 10/8 → 10월 2주차."""
    thursday = monday + timedelta(days=3)
    return f'{thursday.month}월 {(thursday.day - 1) // 7 + 1}주차'


def utc_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """한국 시간 날짜 경계 → staging DB 의 imported_at(UTC, 시간대 없음)과 비교할 값."""
    def conv(d: date) -> datetime:
        return datetime.combine(d, time(), KST).astimezone(timezone.utc).replace(tzinfo=None)
    return conv(start), conv(end)


def headline(label: str, count: int) -> str:
    """원글. 슬랙은 <…> 를 링크로 읽어 글자가 사라진다. &lt; &gt; 로 보내면 <N건 추가> 로 보인다"""
    return f'*:bell: [{label}] 크롤러 작품 추가 알림*\n\n&lt;{count}건 추가&gt;'


def chunks(names: list[str]) -> list[str]:
    """스레드 답글들. THREAD_CHUNK 개씩"""
    return ['\n'.join(names[i:i + THREAD_CHUNK]) for i in range(0, len(names), THREAD_CHUNK)]


def run(store, today: date | None = None, send: bool = True) -> dict:
    today = today or datetime.now(KST).date()
    start, end = last_week(today)
    label = week_label(start)
    names = [r['works_name'] for r in store.created_works(*utc_bounds(start, end))]
    text = headline(label, len(names))
    sent = False
    if names and send:
        ts = notify.post(text)
        sent = bool(ts) and all([notify.slack(c, thread_ts=ts) for c in chunks(names)])
    return {'week': label, 'from': start.isoformat(), 'to': (end - timedelta(days=1)).isoformat(),
            'created': len(names), 'sent': sent, 'text': (text + '\n' + '\n'.join(names)) if names else None}
