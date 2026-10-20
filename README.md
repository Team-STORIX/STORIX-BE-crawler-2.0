<h1>
<img src="https://github.com/user-attachments/assets/b65bf457-6754-4e0d-9d94-4511721b2b36" align=left width=100>
STORIX-BE-Crawler
</h1>

> 이야기에 대한 이야기로 모두가 연결되는 이 곳! 데이터로 취향을 연결하는 웹툰·웹소설 팬덤 플랫폼, STORIX

<br>

웹툰 · 웹소설 플랫폼에서 작품 정보를 모아, 검수를 거쳐 STORIX BE 에 넣습니다.

```
플랫폼 ──수집──▶ JSONL ──▶ staging DB ──검수──▶ BE import API
```

- 서비스 DB 에 직접 쓰지 않습니다. 같은 작품 판정은 BE 가 합니다
- robots.txt 를 지키고 봇임을 밝힙니다 (`STORIXBot/1.0`). 아이디 · 비밀번호 자동 입력은 하지 않습니다
- 서버는 자정에 켜져 노션 작품 추가 요청만 처리하고 꺼집니다

## 지원 플랫폼

| `--platform` | 수집 |
|---|---|
| `naver_webtoon` · `kakao_page` | 목록 · 제목 검색 · 링크 |
| `lezhin` · `mrblue` | 제목 검색 · 링크 |
| `ridibooks` · `naver_series` | 링크로만 (검색이 막힘) |
| 네이버 웹소설 · 봄툰 | 수집 안 함 (robots.txt) |

## 빠른 시작

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

python cli.py login --platform all                                   # 브라우저에서 로그인, 쿠키 저장
python cli.py crawl --platform all --mode search_titles --titles "나 혼자만 레벨업"
python cli.py stage load   --env dev --input 'output/<날짜>/*.jsonl' --source search_titles
python cli.py stage import --env dev --run-id <런>
```

staging DB 는 SSM 터널(`127.0.0.1:13306`)로 붙습니다. 터널 스크립트는 인프라 담당자에게 받습니다.

## 명령

| 명령 | 하는 일 |
|---|---|
| `crawl --mode initial · new_works · update_fields · search_titles · custom_url` | 수집 → `output/<날짜>/*.jsonl` |
| `login` · `keepalive` | 로그인 쿠키 저장 · 갱신 |
| `stage load · stats · import` | staging 적재 · 검사 → BE 반영 |
| `stage promote · recover · queue · suggest · apply · weekly` | 매핑 승격 · 링크 복구 · 바깥 검수 · 주간 알림 |
| `requests` | 노션 작품 추가 요청 처리 (서버 야간 실행) |

`--env dev | prod` 로 BE 대상을 고릅니다 (기본 `STORIX_ENV`, 없으면 dev).

## 문서

| | |
|---|---|
| [수집 상세](crawler/README.md) | 제목 검색 규칙 · 플랫폼별 메모 · JSONL 형식 |
| [검수 · 적재](review/README.md) | 판정 · 매핑 승격 · 링크 복구 · 노션 요청 · Alembic |
| [서버 운영](deploy/README.md) | 야간 실행 · 슬랙 알림 · 세션 만료 대응 · 설정 · 점검 |

## 개발

- `develop` 에 기능 PR → `release/vX.Y.Z` → `main` (BE 와 같은 Release 방식)
- CI: PR · 푸시마다 `pytest` (MySQL 8.0) · Docker 빌드. CD: `main` 푸시 때 ECR 에 이미지
- 로컬은 Docker 대신 `python cli.py` 로 (로그인 창이 필요)

```
cli.py       CLI 진입점
crawler/     수집 모드 · JSONL 기록 · 슬랙 알림 · 원격 로그인
modules/     플랫폼별 크롤러 · robots.txt 검사
review/      검수 파이프라인 (staging DB · 규칙 · 검수 API · BE import)
deploy/      서버 야간 실행 · 쿠키 올리기 · 슬랙 버튼 Lambda
```

<br>

## 🛠️ Developers

### 2026.10 ~ · 검수 · 운영 자동화
<table>
    <tr align="center">
        <td><B>Lead·Backend</B></td>
    </tr>
    <tr align="center">
        <td><B>이수아</B></td>
    </tr>
    <tr align="center">
        <td>
            <img src="https://github.com/Immmii.png?size=100" width="100">
            <br>
            <a href="https://github.com/Immmii"><I>Immmii</I></a>
        </td>
    <tr align="center">
        <td><B>검수 파이프라인·노션 요청 자동화<br>·서버 야간 실행·robots 준수</B></td>
    </tr>
</table>

### 2026.05 ~ 2026.10 · 크롤러 2.0
<table>
    <tr align="center">
        <td><B>Backend</B></td>
    </tr>
    <tr align="center">
        <td><B>이윤지</B></td>
    </tr>
    <tr align="center">
        <td>
            <img src="https://github.com/yooniicode.png?size=100" width="100">
            <br>
            <a href="https://github.com/yooniicode"><I>yooniicode</I></a>
        </td>
    <tr align="center">
        <td><B>크롤러 2.0 리팩터링·리디북스<br>·커스텀 URL·Docker</B></td>
    </tr>
</table>

### 2025.11 · 크롤러 1.0
<table>
    <tr align="center">
        <td><B>Backend</B></td>
    </tr>
    <tr align="center">
        <td><B>서가영</B></td>
    </tr>
    <tr align="center">
        <td>
            <img src="https://github.com/caminobelllo.png?size=100" width="100">
            <br>
            <a href="https://github.com/caminobelllo"><I>caminobelllo</I></a>
        </td>
    <tr align="center">
        <td><B>크롤러 1.0 구축<br>·네이버·카카오페이지</B></td>
    </tr>
</table>
