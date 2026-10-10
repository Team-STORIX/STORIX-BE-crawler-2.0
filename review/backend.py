"""BE 내부 API 호출 세션. 요청마다 X-Internal-Api-Key 헤더를 붙인다.

크롤러용 API 는 관리자 API 와 분리된 /internal/v1/** 이고, 관리자 로그인(JWT) 대신 고정 키로 인증한다.
키는 Parameter Store /storix/{dev,prod}/env/INTERNAL_API_KEY 에 있고, 로컬은 .env(gitignore 됨)에 넣는다.

환경변수: STORIX_API_BASE_URL, STORIX_INTERNAL_API_KEY
"""
import json
import os
import urllib.error
import urllib.request

API_KEY_HEADER = 'X-Internal-Api-Key'


class BackendAuthError(RuntimeError):
    """키가 틀렸거나 없다 — 재시도해도 안 되므로 호출부에서 멈춘다."""


class BackendSession:
    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip('/')
        self._api_key = api_key
        self._timeout = timeout

    @classmethod
    def from_env(cls) -> 'BackendSession | None':
        base_url = os.getenv('STORIX_API_BASE_URL', '')
        api_key = os.getenv('STORIX_INTERNAL_API_KEY', '')
        if not (base_url and api_key):
            return None
        return cls(base_url, api_key)

    def request(self, method: str, path: str, body: dict | None = None, timeout: float | None = None):
        """CustomResponse.result 를 돌려준다. 401 · 403 은 BackendAuthError,
        그 밖의 HTTP 오류(409 등)는 HTTPError 그대로 올린다."""
        try:
            return self._send(method, path, body, timeout)
        except urllib.error.HTTPError as e:
            if e.code not in (401, 403):
                raise
            raise BackendAuthError(f'BE 내부 API 인증 실패: HTTP {e.code} ({self.base_url}) — STORIX_INTERNAL_API_KEY 확인') from None

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
