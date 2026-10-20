# 서버 운영

[← README](../README.md)

## 야간 실행

```
EventBridge Scheduler 00:00 → EC2 켜기
  └ systemd storix-crawler-nightly (deploy/nightly.sh)
      1. Parameter Store /storix/crawler/* → /run/storix-crawler.env (메모리, 끝나면 지움)
      2. main 의 compose · 스크립트, ECR storix-crawler:latest 이미지 받기
      3. S3 에 더 새 쿠키가 있으면 받기
      4. 쿠키 갱신   python cli.py keepalive
      5. docker compose run --rm nightly   (= python cli.py requests --env prod)
      6. S3 보다 새 쿠키를 올리고 끄기
EventBridge Scheduler 01:00 → EC2 끄기 (멈췄을 때 안전망)
```

- **쿠키 갱신(`keepalive`)**: 쿠키 파일이 있는 플랫폼을 모두 쿠키로 열어 봅니다. 열리면 사이트가 새로 준 쿠키를 합쳐 저장하고(쓸 때마다 연장되는 세션 유지), 안 열리면 그날 요청이 없어도 만료 알림을 보냅니다 (같은 날 같은 쿠키는 한 번만)
- 인스턴스 `t3a.small` (CPU 크레딧 standard, 추가 과금 없음). Google Chrome 의 리눅스 ARM 판이 없어 t4g 는 못 씁니다
- 들어오는 연결이 없어 EIP · 열린 포트가 없습니다

## 슬랙 알림

| 알림 | 언제 |
|---|---|
| 세션 만료 · 차단 · 수집 실패 · 예외로 멈춤 | 야간 실행 중 (`AUTH_EXPIRED` · `RATE_LIMITED` · `PARSING_FAILED`) |
| 주간 작품 추가 | 월요일 자정 실행 때 지난주 새로 만든 작품. 원글엔 건수만, 작품명은 스레드 답글로 (없으면 안 보냄). 직접: `stage weekly --env prod [--date YYYY-MM-DD] [--send]` |

- `SLACK_BOT_TOKEN` · `SLACK_CHANNEL_ID`(BE 와 같은 봇)가 없으면(로컬) 보내지 않습니다
- 스레드 답글의 결과 이모지(🔐 · ✅ · ⚠️ · ❌)를 원글에도 달아 목록만 봐도 결과가 보입니다

## 세션이 만료됐을 때

**폰으로 다시 로그인** — 세션 만료 알림의 `[📱 다시 로그인]` 버튼

```
버튼 → Lambda deploy/lambda/slack_actions.py (서명 · 누른 사람 확인) → 서버를 로그인 모드로 켬
  → crawler/remote_login.py: Xvfb + noVNC + Cloudflare 임시 터널 → 원격 화면 링크를 누른 사람에게만 DM
  → 폰에서 그 화면의 Chrome 으로 로그인 (캡차 · 2차 인증 직접)
  → 쿠키를 S3 에 올리고 ✅ → 서버 꺼짐
```

- 링크는 무작위 주소 + 1회용 VNC 비밀번호, 한 기기만, 30분 뒤 닫힘. 들어오는 포트는 열지 않습니다
- 야간 실행 중에 누르면 수집을 마친 뒤 끄기 전에 원격 로그인합니다 (태그 `crawler:mode=login-after`). 켜지는 중 · 꺼지는 중이면 잠시 뒤 다시 눌러 달라고 답합니다
- 세션 만료로 못 찾은 노션 요청은 노션에 표시하지 않고 남겨 둡니다. 다시 로그인하면 다음 자정에 다시 찾습니다
- 로그인 완료 답글의 `[🔄 못 긁은 작품 다시 긁기]` 버튼을 누르면 노션 요청 처리만 바로 다시 돌고 결과를 그 스레드에 답니다
- 크롤러 전용 슬랙 앱(`deploy/slack-app-manifest.yml`), Interactivity Request URL = Lambda Function URL

**맥에서 직접** — 서버가 꺼져 있어도 됩니다. 만료된 플랫폼만 올려야 서버가 저장한 다른 쿠키를 덮지 않습니다.

```bash
python cli.py login --platform kakao_page
deploy/push_sessions.sh kakao               # 인자가 없으면 sessions/*.pkl 전부
```

## 설정 (Parameter Store `/storix/crawler/`)

파라미터 이름이 곧 환경변수 이름입니다.

| 이름 | 값 |
|---|---|
| `MYSQL_DATABASE_HOST` · `MYSQL_DATABASE_PORT` | staging RDS 주소 · 3306 |
| `STAGING_DB_USERNAME` · `STAGING_DB_PASSWORD` | staging 계정 (`MYSQL_DATABASE_USER` · `PASSWORD` 로도 넘김) |
| `NOTION_TOKEN` · `NOTION_REQUEST_DB_ID` | 노션 작품 요청 DB |

BE 와 같이 쓰는 값은 복사하지 않고 `/storix/prod/env/` 에서 읽습니다: `INTERNAL_API_KEY`(→ `STORIX_PROD_INTERNAL_API_KEY`) · `SLACK_BOT_TOKEN` · `SLACK_CHANNEL_ID`

## 처음 설치 · 점검

```bash
sudo git clone https://github.com/Team-STORIX/STORIX-BE-crawler-2.0.git /opt/storix-crawler
sudo cp /opt/storix-crawler/deploy/storix-crawler-nightly.service /etc/systemd/system/
sudo systemctl enable storix-crawler-nightly
```

| 하고 싶은 것 | 방법 |
|---|---|
| 끄지 않고 들어가서 고치기 | `sudo mkdir -p /etc/storix-crawler && sudo touch /etc/storix-crawler/hold` (다 고치면 지움) |
| BE · 노션에 반영 없이 시험 | `hold` 둔 채 `sudo NIGHTLY_ARGS='python cli.py requests --env prod --dry-run' /opt/storix-crawler/deploy/nightly.sh` |
| 로그 | `journalctl -u storix-crawler-nightly`, 수집 리포트 `output/<날짜>/requests_report.json` |

- IAM 역할 `StorixCrawlerRole`: `/storix/crawler/*` · `/storix/prod/env/` 의 위 세 값 읽기, 쿠키 S3 읽기, ECR 받기, SSM 접속

## 세션 등록 API · 세션 등록 툴

**서버** — `api/session_api.py` (`:8100`, `Authorization: Bearer <SESSION_API_TOKEN>`)

```bash
docker compose --profile api up -d session-api
```

| 엔드포인트 | 설명 |
|---|---|
| `GET /health` | 헬스체크 (인증 없음) |
| `GET /sessions` | 플랫폼별 세션 상태 |
| `POST /sessions/{platform}` | 쿠키 JSON 등록 → `sessions/*.pkl` |

**로컬** — `tools/register_session.py`

```bash
pip install selenium requests && python tools/register_session.py
powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1   # 운영 담당자용 exe (tools/dist/세션등록.exe + session_tool.config.json)
```

exe 를 실행 → 크롬에서 로그인 → 자동으로 서버에 올립니다. 설정 파일이 없으면 `sessions/*.pkl` 로 저장합니다.


