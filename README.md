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

## 문서

| | |
|---|---|
| [수집](crawler/README.md) | 설치 · 실행 · 수집 모드 · 제목 검색 규칙 · 플랫폼별 메모 · JSONL 형식 |
| [검수 · 적재](review/README.md) | 판정 · 매핑 승격 · 링크 복구 · 노션 요청 · Alembic |
| [서버 운영](deploy/README.md) | 야간 실행 · 슬랙 알림 · 세션 만료 대응 · 설정 · 점검 |

## 파일 구조

```
.
├── cli.py                  CLI 진입점 (login · keepalive · crawl · stage · requests)
├── config.py               URL · DB · 경로 상수
├── crawler/
│   ├── modes/              initial · new_works · update_fields · search_titles · custom_url · recover · requests
│   ├── output/             JSONL 기록 (platform_work_id 로 중복 제거, 작가명 정규화)
│   ├── notify.py           슬랙 알림
│   └── remote_login.py     폰 원격 로그인 (noVNC + Cloudflare 임시 터널)
├── modules/crawler/
│   ├── base_crawler.py     드라이버 · 상세 재시도 · 세션 복구
│   ├── platforms.py        플랫폼 등록부 (크롤러 · 로그인 · 링크 규칙)
│   ├── robots.py           robots.txt 검사
│   ├── cookies.py · titles.py · classify.py   쿠키 · 제목 규칙 · 작품 유형 판정
│   └── naver · kakao · ridibooks · naver_series · lezhin · mrblue _crawler.py
├── review/                 검수 파이프라인
│   ├── models.py · store.py · migrations/   staging DB 구조 · 쿼리 · Alembic
│   ├── rules.py · catalog.py                Layer 1 규칙 · BE enum 카탈로그
│   ├── service.py · app.py                  적재 · 승인 · 거절, 검수 API
│   └── importer.py · backend.py             BE import API 호출
├── deploy/                 서버 야간 실행 · 쿠키 올리기 · 슬랙 버튼 Lambda · systemd
├── api/ · tools/           세션 등록 API · 툴 (예전 방식)
├── tests/
├── sessions/               로그인 쿠키 (커밋 안 됨)
└── output/<날짜>/          수집 결과 JSONL · 리포트 (커밋 안 됨)
```

## 개발

- `develop` 에 기능 PR → `release/vX.Y.Z` → `main` (BE 와 같은 Release 방식)
- CI: PR · 푸시마다 `pytest` (MySQL 8.0) · Docker 빌드. CD: `main` 푸시 때 ECR 에 이미지

<br>

## 🛠️ Developers

### 2026.10 ~ · 리팩터링 · 검수 · 운영 자동화
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
        <td><B>리팩터링(플랫폼 등록부·공통 모듈·HTTP 우선 수집)<br>·검수 파이프라인·노션 요청 자동화<br>·서버 야간 실행·robots 준수</B></td>
    </tr>
</table>

### 2026.05 ~ 2026.10 · 크롤러 2.0 전환
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
        <td><B>2.0 전환·리디북스·네이버 웹소설<br>·커스텀 URL·Docker</B></td>
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
