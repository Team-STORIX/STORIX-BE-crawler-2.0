# STORIX-BE-Crawler 2.0

네이버 웹툰 / 네이버 웹소설 / 네이버 시리즈 / 카카오페이지 / 리디북스 크롤러
Selenium 병렬 워커 → JSONL 산출물 → DB 배치 적재 파이프라인

---

## 주요 기능

- CLI 진입점 (`cli.py`) — `crawl` / `batch` 명령
- JSONL 산출물 작성기 (`crawler/output/jsonl_writer.py`)
- 크롤링 모드: initial (전체), new_works (신작), update_fields (필드 갱신), search_titles (작품명 검색), **custom_url (URL 기반)**
- 배치 적재: 스키마 검증 → 폴백 복구 → DB INSERT (`batch/`)
- APScheduler 기반 자동 실행 (`scheduler/`)
- 모니터링: 세션 만료 감지, 플랫폼별 실행 이력 (`monitor/`)
- 병렬 워커 풀 (`ThreadPoolExecutor` + `queue.Queue`, 워커 2개)
- 환경변수 자격증명 자동 로그인 → 쿠키 로그인 → 브라우저 수동 로그인 순으로 폴백
- **works_platform 중간 테이블** — 동일 작품이 여러 플랫폼에 연재될 경우 플랫폼 목록 확장
- **검수 큐 대화형 수정** — 검증 실패 레코드를 사용자가 직접 수정
- **JSONL 중복 제거** — `platform_work_id` 기준 in-memory dedup, 재실행 시 기존 파일 로드 후 덮어씌움
- **DB priority 공존 로직** — 새 값·기존 값 모두 있을 때 장르 우선순위(`로판`=5 등)로 선택, 한쪽만 있으면 있는 쪽 채택

---

## 실행 방법

### 로컬 (Python 직접)

**가상환경 생성 및 의존성 설치**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

> PyCharm 사용 시: Settings → Python Interpreter → Add Interpreter → Virtualenv → 프로젝트 루트 선택
> 이후 PyCharm 터미널을 열면 자동으로 `(venv)` 활성화됩니다.

**.env 설정** (`.env.example` 복사 후 수정)
```bash
cp .env.example .env
```

**DB 연결 옵션 (둘 중 하나 선택)**

| 방법 | 설명 |
|------|------|
| SSM 터널 | RDS(프라이빗 서브넷) 접근. 터미널 1에서 터널 유지, 터미널 2에서 크롤러 실행 |
| 로컬 MySQL | `docker compose up db -d` 로 로컬 MySQL 컨테이너 실행. `.env`의 PORT를 `3306`으로 변경 |

```bash
# SSM 터널 방식
./storix-db-tunnel.sh          # 터미널 1: 유지

# 로컬 MySQL 방식
docker compose up db -d
# .env: MYSQL_DATABASE_PORT=3306
```

---

**크롤링**
```bash
# 전체 initial 크롤링 (네이버 + 카카오 + 리디북스)
python cli.py crawl --platform all --mode initial

# 신작 크롤링
python cli.py crawl --platform all --mode new_works

# 특정 플랫폼만
python cli.py crawl --platform kakao_page --mode initial
python cli.py crawl --platform ridibooks --mode initial

# 필드 갱신 (기존 JSONL 재크롤링)
python cli.py crawl --platform all --mode update_fields --input ./output/2026-05-09/

# 작품명 리스트로 검색 크롤링 (파일 입력)
python cli.py crawl --platform naver_webtoon --mode search_titles --titles-file titles.txt

# 작품명 리스트로 검색 크롤링 (쉼표 구분 직접 입력)
python cli.py crawl --platform all --mode search_titles --titles "나 혼자만 레벨업,재혼 황후"

# 네이버 웹소설(novel.naver.com) / 네이버 시리즈(series.naver.com)도 지원
python cli.py crawl --platform naver_novel  --mode search_titles --titles-file titles.txt
python cli.py crawl --platform naver_series --mode search_titles --titles "화산귀환,재벌집 막내아들"

# 커스텀 URL 크롤링 (네이버 웹툰 - 일일 연재+ 인기순, 상위 213개)
python cli.py crawl --mode custom_url --url "https://comic.naver.com/webtoon?tab=dailyPlus" --count 213

# 커스텀 URL 크롤링 (네이버 웹툰 - 완결 인기순, 상위 500개)
python cli.py crawl --mode custom_url --url "https://comic.naver.com/webtoon?tab=finish" --count 500

# 커스텀 URL 크롤링 (카카오페이지, 상위 300개)
python cli.py crawl --mode custom_url --url "https://www.kakaopage.com/content/..." --count 300
```

> **`search_titles` 모드 (임의 목록으로 특정 작품만 채우기)**
> - 지원 플랫폼: `naver_webtoon`, `naver_novel`, `naver_series`, `kakao_page`, `all` (리디북스 미지원)
> - `titles.txt`에 작품명을 줄바꿈으로 나열하면 각 플랫폼에서 제목 검색 → 상세 크롤 → JSONL 저장.
> - `--platform all`은 위 4개 플랫폼을 순회하며, **한 곳에서라도 찾으면** 해당 작품을 처리합니다.
> - 제목 매칭은 **완전일치 → 부분일치(포함) → 유사도 ≥90%** 순으로 시도합니다. 정규화(공백·괄호·`·∙#` 제거) 후 비교하며, 유사 매칭 채택 시 로그에 `(유사 92%)`처럼 점수를 남깁니다. 임계값은 `BaseCrawler.TITLE_FUZZY_THRESHOLD`로 조정.
> - **네이버 웹소설**은 정식(시리즈에디션)에 없으면 **베스트리그·챌린지리그(베스트도전)**까지 검색합니다. 리그 우선순위는 `정식 > 베스트 > 챌린지`, 로그에 `(베스트리그·정확)`처럼 출처를 표기합니다. 단 아마추어 리그는 2차창작·팬픽 오탐을 막기 위해 **부분일치를 제외하고 완전일치·유사도만** 인정합니다.
> - **`--titles-file` 사용 시 크롤에 성공한 작품은 파일에서 자동 제거**되어, `titles.txt`에는 못 찾은 작품만 남습니다(재시도용). `--titles`(직접 입력)는 파일을 수정하지 않습니다.
> - 이후 `batch import`가 빈 필드만 `COALESCE`로 채우므로 기존 값은 보존됩니다.

**해시태그/플랫폼 빈 works 채우기** (`scripts/fill_missing_works.py`)

`works_hashtag` 또는 `works_platform`이 하나도 없는 works를 찾아, `works_type`(웹툰/웹소설)별 섹션 헤더 형식으로 `titles.txt`를 생성합니다. 그대로 `search_titles`에 넣어 재크롤하면 빈 해시태그·플랫폼이 채워집니다.

```bash
# 1단계: 대상 조회 (읽기 전용, 파일 미변경 — 미리보기만)
python scripts/fill_missing_works.py

# 2단계: titles.txt 에 기록 (-o 로 경로 변경 가능)
python scripts/fill_missing_works.py --write

# 3단계: 재크롤
python cli.py crawl --platform all --mode search_titles --titles-file titles.txt

# 4단계: 중복 없이 DB 반영 (fill_import.py — 일반 batch import 대신 사용)
python scripts/fill_import.py --input output/2026-07-25/search_titles.jsonl --dry-run  # 분류 미리보기
python scripts/fill_import.py --input output/2026-07-25/search_titles.jsonl            # 실제 반영
```

> - 기본은 **읽기 전용**이며, 실제 기록은 `--write`가 있어야 합니다(`titles.txt` 실수 덮어쓰기 방지).
> - 해시태그 0 / 플랫폼 0 건수를 나눠 요약 출력합니다.
> - `works_type`이 웹툰/웹소설이 아닌 행은 `search_titles`가 스킵하므로 파일에 넣지 않고 **경고로 따로 보고**합니다(수동 처리 필요).

**왜 `fill_import.py`인가 (중복 행 방지)**

일반 `batch import`는 `(works_name + artist_name)`으로 기존 행을 찾으므로, 재크롤한 작가명이 DB와 조금이라도 다르면 **빈 행을 채우는 대신 새 행을 하나 더 만듭니다.** [scripts/fill_import.py](scripts/fill_import.py)는 `works_name`으로 기존 행을 찾아 이렇게 분기합니다:

| 상황 | 처리 |
|------|------|
| 크롤 작가 == 기존 작가 | 그 행을 정상 채움 (`save_one_row`) |
| 기존 작가가 비어있음 | 그 행에 작가명 세팅 후 채움 (ADOPT) |
| 기존 작가가 이미 다른 값 | 채우지 않고 **검수큐(별도 폴더)에 저장** [CONFLICT] |
| 빈 작가 행 다수 / 매칭 없음 / 크롤 작가 없음 | 검수큐 |

> - 채우는 값의 병합 규칙은 `save_one_row`와 동일(빈 값 유지·채워진 값 반영, 해시태그는 있을 때만 교체, 플랫폼은 `INSERT IGNORE` 추가).
> - 검수큐는 기본 `output/fill_review/artist_conflict_queue.jsonl`에 쌓이며 `python cli.py batch review --input <경로>`로 조회. `--review-dir`로 위치 변경 가능.

**배치 적재**
```bash
# 당일 폴더 자동 감지 후 DB 적재 (크롤링 직후 권장)
python cli.py batch import

# 특정 날짜 폴더 전체 DB 적재
python cli.py batch import --input ./output/2026-07-17/

# 감시 모드 (새 파일 생성 시 자동 적재)
python cli.py batch import --input ./output/ --watch

# 수동 검수 큐 확인
python cli.py batch review --input ./output/2026-05-09/manual_review_queue.jsonl

# 검수 큐 대화형 수정 (당일 폴더 자동 감지)
python cli.py batch fix

# 특정 날짜의 검수 큐 수정
python cli.py batch fix --input ./output/2026-05-09/manual_review_queue.jsonl
```

**검수 큐 수정 후 DB 적재**
```bash
# 1단계: 검수 큐 대화형 수정 (고정된 레코드는 fixed_records.jsonl로 저장)
python cli.py batch fix

# 2단계: 수정된 레코드를 DB에 적재
python cli.py batch import --input ./output/2026-05-14/fixed_records.jsonl
```

**스케줄러**
```bash
python -m scheduler.runner
```

---

### 로그인 및 세션 관리

로그인은 아래 순서로 자동 폴백됩니다.

| 순서 | 방법 | 설명 |
|------|------|------|
| 1 | 쿠키 파일 | `sessions/*.pkl` 존재 시 자동 적용 |
| 2 | 환경변수 자격증명 | `.env`의 `NAVER_ID/PW`, `KAKAO_ID/PW`, `RIDIBOOKS_ID/PW` 설정 시 자동 로그인 시도 |
| 3 | 브라우저 수동 로그인 | 위 두 방법 실패 시 브라우저 창이 열리고 로그인 완료를 자동 감지 |

`.env`에 자격증명을 등록해두면 쿠키 만료 시 재로그인이 자동으로 처리됩니다.

```env
NAVER_ID=your_naver_id
NAVER_PW=your_naver_password
KAKAO_ID=your_kakao_email
KAKAO_PW=your_kakao_password
RIDIBOOKS_ID=your_ridibooks_email
RIDIBOOKS_PW=your_ridibooks_password
```

> CAPTCHA가 트리거되면 자격증명 로그인이 실패하고 수동 로그인으로 폴백됩니다.
> 수동 로그인 시 브라우저에서 로그인을 완료하면 자동 감지되며 (최대 3분 대기), 완료 후 쿠키를 저장합니다.

**카카오페이지 최초 로그인 시 주의사항**
1. 브라우저에서 카카오 로그인을 완료하세요.
2. 성인 웹툰을 하나 클릭해 '연령 확인'이 뜨면 인증을 완료하세요.
3. 자동으로 감지됩니다.

크롤링 중 세션이 만료되면 자동으로 드라이버를 재시작하고 재로그인 후 크롤링을 재개합니다.

쿠키 파일(`sessions/*.pkl`)은 `.gitignore`에 등록되어 있어 커밋되지 않습니다.

---

### 로그인 세션 저장 (Docker 실행 전 필수)

Docker는 GUI가 없는 headless 환경이라 최초 로그인은 **로컬 Python에서 먼저** 실행해야 합니다.
`python cli.py login` 명령이 브라우저를 열고 로그인을 완료하면 `sessions/*.pkl`에 쿠키를 저장합니다.

```bash
# 플랫폼별 개별 로그인
python cli.py login --platform naver_webtoon
python cli.py login --platform kakao_page
python cli.py login --platform ridibooks

# 전체 한 번에 (순서대로 실행)
python cli.py login --platform all
```

로그인 방법은 자동 폴백 순서를 따릅니다.
1. `.env`에 자격증명(`NAVER_ID/PW` 등)이 있으면 자동 로그인 시도
2. CAPTCHA 등으로 실패하면 브라우저 창이 열리고 수동 로그인 완료를 자동 감지
3. 로그인 완료 후 `sessions/` 폴더에 쿠키 파일 저장

> **카카오페이지**: 최초 로그인 시 성인 웹툰을 한 번 클릭해 연령 확인을 완료해야 성인 콘텐츠 크롤링이 가능합니다.

---

### Docker

로컬 컴퓨터에서 스케줄러를 상시 운영할 때 편리합니다. 먼저 위의 로그인 세션 저장을 완료한 뒤 실행하세요.

```bash
# 1단계: 로그인 세션 저장 (로컬 Python, 최초 1회 또는 쿠키 만료 시)
python cli.py login --platform all

# 2단계: 스케줄러 상시 실행 (백그라운드)
docker compose --profile scheduler up -d

# DB만 실행 (로컬 개발용)
docker compose up db -d

# 크롤링 단발 실행
docker compose --profile crawl up

# 배치 적재 단발 실행
docker compose --profile batch up
```

쿠키가 만료되면 스케줄러가 자동 재로그인을 시도합니다. `.env`에 자격증명이 없거나 CAPTCHA가 발생한 경우에는 로컬에서 `python cli.py login --platform <platform>` 을 다시 실행한 뒤 컨테이너를 재시작하세요.

```bash
docker compose --profile scheduler restart
```

> Apple Silicon(M1/M2/M3) Mac에서는 Dockerfile에 `--platform=linux/amd64`가 설정되어 있습니다.
> Chrome + MySQL을 동시에 실행하면 메모리 부족이 발생할 수 있으니, 로컬에서는 `docker compose up db`만 사용하고 크롤러는 Python으로 직접 실행하세요.

---

### 세션 등록 API + 세션등록 툴 (원격 서버 운영용)

서버에는 GUI가 없으므로, 세션(쿠키)은 로컬에서 추출해 API로 등록합니다.

**서버 측** — 세션 등록 API (`api/session_api.py`):

```bash
# .env에 SESSION_API_TOKEN=<임의의 긴 토큰> 추가 후
docker compose --profile api up -d session-api   # :8100
```

| 엔드포인트 | 설명 |
|---|---|
| `GET /health` | 헬스체크 (인증 불필요) |
| `GET /sessions` | 플랫폼별 세션 등록 상태 조회 |
| `POST /sessions/{platform}` | 쿠키 JSON 등록 → `sessions/*.pkl` 저장 |

인증은 `Authorization: Bearer <SESSION_API_TOKEN>` 헤더를 사용합니다.

**로컬 측** — 세션 추출 툴 (`tools/register_session.py`):

```bash
# 개발자: 직접 실행
pip install selenium requests
python tools/register_session.py

# 기획/운영 배포용: exe 빌드
powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1
# → tools/dist/세션등록.exe + session_tool.config.json 을 함께 전달
```

exe를 더블클릭 → 뜨는 크롬 창에서 로그인(2차인증 포함) → 자동 감지 후 서버 업로드까지 완료됩니다.
`session_tool.config.json`이 없으면 `sessions/*.pkl` 파일 저장 모드로 동작합니다.

---

### 배포 (CI/CD)

`develop`/`main` 푸시 시 GitHub Actions가 자동으로 빌드·배포합니다.

1. **CI** (`.github/workflows/ci.yml`): ECR에 이미지 빌드/푸시 — 태그 `{dev|prod}-{sha}`, `{dev|prod}-latest`
2. **CD** (`.github/workflows/cd.yml`): CI 성공 시 SSM으로 EC2에 접속해 `docker-compose.prod.yml` 기준 `pull` + `up -d` (scheduler, session-api)

필요한 레포 시크릿: `APP_DEPLOY_ROLE_ARN`, `ECR_REGISTRY`, `PROD_INSTANCE_ID`, `DEV_INSTANCE_ID`
서버 전제: `/home/ubuntu/storix-crawler/`에 `docker-compose.prod.yml`과 `.env` 배치, ECR에 `storix-crawler` 리포지토리 생성

---

## 스케줄 테이블 (기본값, Asia/Seoul)

| 잡 | 주기 | 시간 |
|----|------|------|
| initial (네이버) | 매월 1일 | 03:00 |
| initial (카카오) | 매월 1일 | 06:00 |
| initial (리디북스) | 매월 1일 | 09:00 |
| new_works (네이버) | 매일 | 09:00 |
| new_works (카카오) | 매일 | 09:30 |
| update_fields (네이버) | 매주 월요일 | 02:00 |
| update_fields (카카오) | 매주 월요일 | 02:30 |

환경변수로 오버라이드 가능: `SCHED_NEW_WORKS_HOUR`, `SCHED_INITIAL_NAVER_HOUR`, `SCHED_INITIAL_RIDIBOOKS_HOUR` 등

---

## 플랫폼 식별자

| CLI `--platform` | DB `platform` 값 | 비고 |
|------------------|-----------------|------|
| `naver_webtoon`  | `NAVER_WEBTOON`  | comic.naver.com |
| `naver_novel`    | `NAVER_NOVEL`    | novel.naver.com (search_titles 지원) |
| `naver_series`   | `NAVER_SERIES`   | series.naver.com — 웹소설·웹툰 단행본 (search_titles 지원) |
| `kakao_page`     | `KAKAO_PAGE`     | page.kakao.com |
| `ridibooks`      | `RIDIBOOKS`      | ridibooks.com |
| -                | `BOMTOON`        | bomtoon.com |

- CLI `--platform` 값은 크롤러 선택 및 파일명 구분에 사용됩니다.
- DB `platform` 값은 `works_platform` 테이블의 `platform` 컬럼에 그대로 적재됩니다.

---

## DB 스키마

### works_platform 중간 테이블

동일 작품(`works_name + artist_name` 기준)이 여러 플랫폼에서 발견될 경우, `works` 행은 하나로 유지하고 `works_platform` 테이블에 플랫폼을 추가합니다.

```
works (works_id, works_name, artist_name, ...)
  ↕  1:N
works_platform (works_id, platform)
```

**예시**: 동일 작품이 네이버와 카카오에 모두 연재 중인 경우
```
works:          { works_id: 1, works_name: "작품명", artist_name: "작가명", ... }
works_platform: { works_id: 1, platform: "NAVER_WEBTOON" }
                { works_id: 1, platform: "KAKAO_PAGE" }
```

---

## 파일 구조 및 역할

```
.
├── cli.py                          # CLI 진입점 (crawl / batch 명령)
├── config.py                       # URL, DB, 경로, 로그인 자격증명 상수
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example                    # 환경변수 템플릿
│
├── sessions/                       # 쿠키 파일 저장소 (.gitignore 처리됨)
│   ├── naver_cookies.pkl
│   └── kakao_cookies.pkl
│
├── crawler/
│   ├── modes/
│   │   ├── initial.py              # 전체 작품 크롤링 (네이버/카카오 섹션별 + 리디북스 카테고리), 워커 2개
│   │   ├── new_works.py            # 신작 탭 크롤링
│   │   ├── update_fields.py        # source_url 목록으로 필드 재크롤링
│   │   └── search_titles.py        # 작품명 리스트 → 플랫폼별 검색 → JSONL 저장, 성공분은 titles-file에서 자동 제거
│   └── output/
│       └── jsonl_writer.py         # 크롤 결과를 JSONL로 기록, platform_work_id 기준 중복 제거, artist_name 정규화
│
├── modules/
│   ├── crawler/
│   │   ├── base_crawler.py         # WebDriver 초기화, crawl_detail_with_retry(), 세션 복구
│   │   ├── naver_crawler.py        # 네이버 웹툰 크롤러 (genre # 제거, hashtags 빈 문자열 필터)
│   │   ├── kakao_crawler.py        # 카카오페이지 크롤러 (작품명 UI 레이블 제거, description \n 보존)
│   │   ├── naver_novel_crawler.py  # 네이버 웹소설 크롤러 (novel.naver.com, 장르목록·상세·제목검색)
│   │   ├── naver_series_crawler.py # 네이버 시리즈 크롤러 (series.naver.com, 웹소설·웹툰 단행본, 제목검색)
│   │   └── ridibooks_crawler.py    # 리디북스 크롤러 (작가 추출 다중 셀렉터, 비정상 URL 필터)
│   ├── db_handler.py               # MySQL 연결, save_one_row() (priority 공존 upsert), works_platform 연동
│   └── logger.py                   # 구조화 로거 팩토리
│
├── batch/
│   ├── validator.py                # JSONL 레코드 스키마/값 검증
│   ├── fallback.py                 # 누락 필드 복구, 유사도 검색, 수동 검수 큐 기록
│   └── importer.py                 # JSONL → DB 적재, --watch 감시 모드
│
├── scheduler/
│   ├── jobs.py                     # 크롤링 잡 함수 (6개)
│   └── runner.py                   # APScheduler BlockingScheduler 실행기
│
├── monitor/
│   ├── session_watcher.py          # 로그인 리다이렉트 감지, 연속 None 경보
│   └── platform_status.py         # 플랫폼별 실행 이력 기록 및 요약 출력
│
├── scripts/                        # 운영·정비용 단발 스크립트
│   ├── diagnose_empty_desc.py      # description 빈 works 원인 분류 (읽기 전용)
│   ├── fill_missing_works.py       # 해시태그/플랫폼 빈 works → titles.txt 생성 (재크롤용)
│   ├── fill_import.py              # 채우기 크롤 결과를 중복 없이 DB 반영, 작가 충돌은 검수큐로
│   └── fix_series_age_badge.py     # 적재된 NAVER_SERIES '19' 배지 오염 제목 재크롤·정리
│
└── output/                         # 크롤링 산출물 (날짜별 JSONL, .gitignore 처리됨)
    └── YYYY-MM-DD/
        ├── initial.jsonl           # platform_work_id 기준 중복 제거, 재실행 시 덮어씌움
        ├── new_works.jsonl
        ├── search_titles.jsonl
        ├── update_fields.jsonl
        ├── naver_webtoon_initial.jsonl # 스케줄러 실행 시 플랫폼별 파일
        ├── manual_review_queue.jsonl
        └── platform_status.jsonl
```

---

## 출력 JSONL 스키마 (schema_version: 2.0)

```json
{
  "schema_version": "2.0",
  "crawled_at": "2026-05-09T03:00:00Z",
  "platform": "naver_webtoon",
  "mode": "initial",
  "works_name": "작품명",
  "platform_work_id": "12345",
  "source_url": "https://comic.naver.com/webtoon/list?titleId=12345",
  "artist_name": "작가1, 작가2",
  "author": "작가1",
  "illustrator": "작가2",
  "original_author": "",
  "age_classification": "전체연령가",
  "description": "작품 설명",
  "genre": "로판",
  "hashtags": ["태그1", "태그2"],
  "thumbnail_url": "https://...",
  "works_type": "웹툰"
}
```
