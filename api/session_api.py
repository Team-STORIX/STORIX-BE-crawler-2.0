"""세션 등록 API.

로컬 추출 툴(tools/register_session.py)이 보낸 쿠키 JSON을 받아
크롤러가 읽는 sessions/*.pkl 형식으로 저장한다.

실행:
    SESSION_API_TOKEN=<토큰> uvicorn api.session_api:app --host 0.0.0.0 --port 8100

pkl을 직접 업로드받지 않고 JSON → 서버에서 pickle 변환만 한다.
(pickle은 로드 시 임의 코드 실행이 가능하므로 외부 입력으로 받으면 안 됨)
"""
import os
import pickle
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from config import (
    KAKAO_COOKIE_FILE,
    NAVER_COOKIE_FILE,
    RIDIBOOKS_COOKIE_FILE,
    SESSIONS_DIR,
)

API_TOKEN = os.getenv('SESSION_API_TOKEN', '')

# platform → (pkl 경로, 로그인 상태를 증명하는 필수 쿠키 이름)
_PLATFORMS: dict[str, tuple[Path, str]] = {
    'naver_webtoon': (NAVER_COOKIE_FILE, 'NID_AUT'),
    'kakao_page': (KAKAO_COOKIE_FILE, '_kawlt'),
    'ridibooks': (RIDIBOOKS_COOKIE_FILE, 'ridi-at'),
}

app = FastAPI(title='STORIX Crawler Session API', version='1.0')


class Cookie(BaseModel):
    name: str = Field(min_length=1)
    value: str
    domain: str = ''
    path: str = '/'
    secure: bool = False
    httpOnly: bool = False
    expiry: int | None = None

    def to_selenium(self) -> dict:
        d = {
            'name': self.name,
            'value': self.value,
            'domain': self.domain,
            'path': self.path,
            'secure': self.secure,
            'httpOnly': self.httpOnly,
        }
        if self.expiry is not None:
            d['expiry'] = self.expiry
        return d


def _auth(authorization: str = Header(default='')) -> None:
    if not API_TOKEN:
        raise HTTPException(503, 'SESSION_API_TOKEN이 설정되지 않았습니다. 서버 환경변수를 확인하세요.')
    if authorization != f'Bearer {API_TOKEN}':
        raise HTTPException(401, '인증 토큰이 올바르지 않습니다.')


@app.get('/health')
def health():
    return {'status': 'ok'}


@app.get('/sessions', dependencies=[Depends(_auth)])
def list_sessions():
    result = []
    for platform, (pkl_path, _) in _PLATFORMS.items():
        exists = pkl_path.exists()
        result.append({
            'platform': platform,
            'registered': exists,
            'updated_at': (
                datetime.fromtimestamp(pkl_path.stat().st_mtime, tz=timezone.utc).isoformat()
                if exists else None
            ),
        })
    return result


@app.post('/sessions/{platform}', dependencies=[Depends(_auth)])
def register_session(platform: str, cookies: list[Cookie]):
    if platform not in _PLATFORMS:
        raise HTTPException(404, f'지원하지 않는 플랫폼: {platform} (지원: {", ".join(_PLATFORMS)})')
    if not cookies:
        raise HTTPException(422, '쿠키가 비어 있습니다.')

    pkl_path, marker = _PLATFORMS[platform]
    names = {c.name for c in cookies}
    if marker not in names:
        raise HTTPException(
            422,
            f'로그인 쿠키({marker})가 없습니다. 로그인이 완료된 상태의 쿠키인지 확인하세요.',
        )

    data = [c.to_selenium() for c in cookies]

    # 크롤러가 읽는 도중 깨진 파일을 보지 않도록 임시 파일에 쓰고 교체
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=SESSIONS_DIR, suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as f:
            pickle.dump(data, f)
        os.replace(tmp, pkl_path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    return {'platform': platform, 'saved': len(data), 'file': pkl_path.name}
