"""주간 "크롤러 작품 추가" 알림 (#77). 월요일 자정 실행 때 지난주(월~일, 한국 시간)에 BE 에 새로 만든 작품을 보낸다.

원글은 안내만, 작품 목록은 그 원글의 스레드 답글로. 답글은 플랫폼마다 하나 (100줄 넘으면 나눠서) (#109):

    *:bell: [10월 2주차] 크롤러 작품 추가 알림*

    <N건 추가>
      └ *카카오페이지* (2)
        - [웹툰] 작품명1
        - [웹툰/웹소설] 작품명2      ← 같은 이름으로 웹툰 · 웹소설이 따로 추가됨

새로 만든 작품 = works_staging.import_result 가 CREATED. 갱신(UPDATED · UNCHANGED)은 넣지 않는다.
노션 요청뿐 아니라 로컬 stage import 로 넣은 것도 같은 staging DB 를 거치므로 함께 잡힌다.
"""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from crawler import notify
from modules.crawler import platforms

KST = ZoneInfo('Asia/Seoul')
THREAD_CHUNK = 100  # 스레드 답글 하나에 넣는 작품 수
TYPE_ORDER = ('웹툰', '웹소설', '만화')


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


def _platform_label(enum: str) -> str:
    try:
        return platforms.get(enum).label
    except KeyError:
        return enum or '기타'


def sections(rows: list[dict]) -> list[tuple[str, list[str]]]:
    """작품들 → [(플랫폼 표시명, ['- [웹툰/웹소설] 작품명', …])]. 플랫폼은 등록부 순서, 작품은 추가된 순서.
    같은 플랫폼에서 같은 이름이 여러 유형으로 추가됐으면 한 줄로 합친다."""
    order = {p.enum: i for i, p in enumerate(platforms.PLATFORMS)}
    grouped: dict[str, dict[str, set[str]]] = {}
    for r in rows:
        for enum in (r.get('platforms') or '').split(',') or ['']:
            types = grouped.setdefault(enum.strip(), {}).setdefault(r['works_name'], set())
            if r.get('works_type'):
                types.add(r['works_type'])
    out = []
    for enum in sorted(grouped, key=lambda e: (order.get(e, len(order)), e)):
        lines = []
        for name, types in grouped[enum].items():
            ordered = sorted(types, key=lambda t: (TYPE_ORDER.index(t) if t in TYPE_ORDER else len(TYPE_ORDER), t))
            lines.append(f"- [{'/'.join(ordered)}] {name}" if ordered else f'- {name}')
        out.append((_platform_label(enum), lines))
    return out


def chunks(groups: list[tuple[str, list[str]]]) -> list[str]:
    """스레드 답글들. 플랫폼마다 하나, THREAD_CHUNK 줄이 넘으면 나눈다 (제목에 '이어서')"""
    out = []
    for label, lines in groups:
        for i in range(0, len(lines), THREAD_CHUNK):
            title = f'*{label}* ({len(lines)})' if i == 0 else f'*{label}* (이어서)'
            out.append(title + '\n' + '\n'.join(lines[i:i + THREAD_CHUNK]))
    return out


def run(store, today: date | None = None, send: bool = True) -> dict:
    today = today or datetime.now(KST).date()
    start, end = last_week(today)
    label = week_label(start)
    rows = store.created_works(*utc_bounds(start, end))
    text = headline(label, len(rows))
    replies = chunks(sections(rows))
    sent = False
    if rows and send:
        ts = notify.post(text)
        sent = bool(ts) and all([notify.slack(c, thread_ts=ts) for c in replies])
    return {'week': label, 'from': start.isoformat(), 'to': (end - timedelta(days=1)).isoformat(),
            'created': len(rows), 'sent': sent, 'text': '\n\n'.join([text, *replies]) if rows else None}
