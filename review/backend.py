"""BE 호출 세션. ADMIN 계정으로 직접 로그인하고, 토큰이 만료되면 한 번 다시 로그인한다.

토큰을 사람이 붙여넣는 구조로는 자동화가 안 돼서 계정을 받는다(.env, gitignore 됨).
storix MCP 의 토큰은 MCP 프로세스 메모리에만 있어 여기서 꺼내 쓸 수 없다.

환경변수: STORIX_API_BASE_URL, STORIX_ADMIN_EMAIL, STORIX_ADMIN_PASSWORD
"""
import json
import os
import urllib.error
import urllib.request

LOGIN_PATH = '/api/v1/auth/admin/login'


class BackendAuthError(RuntimeError):
    """로그인 실패. 계정이 틀렸거나 ADMIN 이 아니다 — 재시도해도 안 되므로 호출부에서 멈춘다."""


class BackendSession:
    def __init__(self, base_url: str, email: str, password: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip('/')
        self._email = email
        self._password = password
        self._timeout = timeout
        self._token: str | None = None

    @classmethod
    def from_env(cls) -> 'BackendSession | None':
        base_url = os.getenv('STORIX_API_BASE_URL', '')
        email = os.getenv('STORIX_ADMIN_EMAIL', '')
        password = os.getenv('STORIX_ADMIN_PASSWORD', '')
        if not (base_url and email and password):
            return None
        return cls(base_url, email, password)

    def request(self, method: str, path: str, body: dict | None = None, timeout: float | None = None):
        """CustomResponse.result 를 돌려준다. 401 이면 다시 로그인해서 한 번만 재시도한다.
        그 밖의 HTTP 오류(409 등)는 HTTPError 그대로 올린다."""
        if self._token is None:
            self._login()
        try:
            return self._send(method, path, body, self._token, timeout)
        except urllib.error.HTTPError as e:
            if e.code != 401:
                raise
        self._login()
        return self._send(method, path, body, self._token, timeout)

    def _login(self) -> None:
        try:
            result = self._send('POST', LOGIN_PATH, {'email': self._email, 'password': self._password}, None, None)
        except urllib.error.HTTPError as e:
            # 계정 문제만 인증 실패로 본다. 5xx 등은 서버 문제라 그대로 올린다
            if e.code not in (400, 401, 403, 422):
                raise
            raise BackendAuthError(f'BE ADMIN 로그인 실패: HTTP {e.code} ({self._email})') from None
        token = (result or {}).get('accessToken')
        if not token:
            raise BackendAuthError('BE ADMIN 로그인 응답에 accessToken 이 없음')
        self._token = token

    def _send(self, method: str, path: str, body: dict | None, token: str | None, timeout: float | None):
        headers = {'Accept': 'application/json'}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        if token:
            headers['Authorization'] = f'Bearer {token}'
        req = urllib.request.Request(self.base_url + path, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout or self._timeout) as resp:
            payload = json.loads(resp.read().decode('utf-8'))
        return payload.get('result', payload) if isinstance(payload, dict) else payload
