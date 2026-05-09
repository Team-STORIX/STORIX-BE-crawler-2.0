import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime

log = logging.getLogger(__name__)

_LOGIN_SIGNATURES: dict[str, list[str]] = {
    'naver_webtoon': ['nid.naver.com', 'naver.com/nidlogin'],
    'kakao_page': ['accounts.kakao.com', 'kauth.kakao.com'],
}

_NULL_STREAK_THRESHOLD = 5


@dataclass
class SessionState:
    platform: str
    is_alive: bool = True
    last_check: datetime = field(default_factory=datetime.now)
    null_streak: int = 0
    alert_count: int = 0


class SessionWatcher:
    def __init__(self):
        self._states: dict[str, SessionState] = {}
        self._drivers: dict[str, object] = {}
        self._callbacks: dict[str, list] = {}
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def register(self, platform: str, driver, on_expire=None) -> None:
        self._states[platform] = SessionState(platform=platform)
        self._drivers[platform] = driver
        if on_expire:
            self._callbacks.setdefault(platform, []).append(on_expire)

    def unregister(self, platform: str) -> None:
        self._states.pop(platform, None)
        self._drivers.pop(platform, None)
        self._callbacks.pop(platform, None)

    def record_null(self, platform: str) -> None:
        state = self._states.get(platform)
        if not state:
            return
        state.null_streak += 1
        if state.null_streak >= _NULL_STREAK_THRESHOLD:
            self._alert(platform, f'연속 None {state.null_streak}회 (세션 만료 의심)')

    def record_success(self, platform: str) -> None:
        state = self._states.get(platform)
        if state:
            state.null_streak = 0
            state.is_alive = True

    def check_now(self) -> dict[str, bool]:
        return {p: self._check_driver(p, d) for p, d in list(self._drivers.items())}

    def _check_driver(self, platform: str, driver) -> bool:
        sigs = _LOGIN_SIGNATURES.get(platform, [])
        try:
            current_url = driver.current_url or ''
        except Exception:
            self._alert(platform, '드라이버 응답 없음 (세션 종료)')
            return False

        for sig in sigs:
            if sig in current_url:
                self._alert(platform, f'로그인 리다이렉트 감지 → {current_url}')
                return False

        state = self._states.get(platform)
        if state:
            state.is_alive = True
            state.last_check = datetime.now()
        return True

    def _alert(self, platform: str, reason: str) -> None:
        state = self._states.get(platform)
        if state:
            state.is_alive = False
            state.alert_count += 1
        log.warning('[SessionWatcher] 🔴 [%s] 세션 경보: %s', platform, reason)
        for cb in self._callbacks.get(platform, []):
            try:
                cb(platform)
            except Exception as e:
                log.error('[SessionWatcher] 콜백 오류: %s', e)

    def start(self, interval: int = 60) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()

        def _loop():
            log.info('[SessionWatcher] 감시 시작 (간격: %ds)', interval)
            while not self._stop_event.is_set():
                self.check_now()
                self._stop_event.wait(interval)
            log.info('[SessionWatcher] 감시 종료')

        self._thread = threading.Thread(target=_loop, daemon=True, name='session-watcher')
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def status(self) -> list[dict]:
        return [
            {
                'platform': s.platform,
                'is_alive': s.is_alive,
                'null_streak': s.null_streak,
                'alert_count': s.alert_count,
                'last_check': s.last_check.strftime('%H:%M:%S'),
            }
            for s in self._states.values()
        ]


watcher = SessionWatcher()
