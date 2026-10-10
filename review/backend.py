"""BE 내부 API 호출 세션. 요청마다 X-Internal-Api-Key 헤더를 붙인다.

크롤러용 API 는 관리자 API 와 분리된 /internal/v1/** 이고, 관리자 로그인(JWT) 대신 고정 키로 인증한다.
키는 Parameter Store /storix/{dev,prod}/env/INTERNAL_API_KEY 에 있고, 로컬은 .env(gitignore 됨)에 넣는다.
대상 환경 · 환경변수는 review/env.py 참고.
"""
import json
import urllib.error
import urllib.request

from review import env as storix_env

API_KEY_HEADER = 'X-Internal-Api-Key'


class BackendAuthError(RuntimeError):
    """키가 틀렸거나 없다 — 재시도해도 안 되므로 호출부에서 멈춘다."""


class BackendSession:
    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0, env: str = ''):
        self.env = env
        self.base_url = base_url.rstrip('/')
        self._api_key = api_key
        self._timeout = timeout

    @classmethod
    def from_env(cls) -> 'BackendSession | None':
        """STORIX_ENV 에 맞는 주소 · 키로 만든다. 키가 없으면 None."""
        env = storix_env.target()
        api_key = storix_env.internal_api_key(env)
        if not api_key:
            return None
        return cls(storix_env.api_base_url(env), api_key, env=env)

    def request(self, method: str, path: str, body: dict | None = None, timeout: float | None = None):
        """CustomResponse.result 를 돌려준다. 401 · 403 은 BackendAuthError,
        그 밖의 HTTP 오류(409 등)는 HTTPError 그대로 올린다."""
        try:
            return self._send(method, path, body, timeout)
        except urllib.error.HTTPError as e:
            if e.code not in (401, 403):
                raise
            key_name = f'STORIX_{self.env.upper()}_INTERNAL_API_KEY' if self.env else '내부 API 키'
            raise BackendAuthError(f'BE 내부 API 인증 실패: HTTP {e.code} ({self.base_url}) — {key_name} 확인') from None

    def _send(self, method: str, path: str, body: dict | None, timeout: float | None):
        headers = {'Accept': 'application/json', API_KEY_HEADER: self._api_key}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(self.base_url + path, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout or self._timeout) as resp:
            payload = json.loads(resp.read().decode('utf-8'))
        return payload.get('result', payload) if isinstance(payload, dict) else payload
