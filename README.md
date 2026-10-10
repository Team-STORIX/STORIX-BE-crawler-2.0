# STORIX-BE-Crawler 2.0

네이버 웹툰 / 네이버 웹소설 / 네이버 시리즈 / 카카오페이지 / 리디북스 크롤러
Selenium 병렬 워커 → JSONL 산출물 → 검수 staging(Layer 1 · 1.5) → BE import API

---

## 주요 기능

- CLI 진입점 (`cli.py`) — `login` / `crawl` / `stage` 명령
- JSONL 산출물 작성기 (`crawler/output/jsonl_writer.py`)
- 크롤링 모드: initial (전체), new_works (신작), update_fields (필드 갱신), search_titles (작품명 검색), **custom_url (URL 기반)**
- 검수 파이프라인 (`review/`): JSONL → staging DB → 규칙 검사(Layer 1) · 런 이상 차단(Layer 1.5) → 사람 검수 → BE import API. **크롤러는 서비스 DB에 직접 쓰지 않습니다**
- APScheduler 기반 자동 실행 (`scheduler/`)
- 병렬 워커 풀 (`ThreadPoolExecutor` + `queue.Queue`, 워커 2개)
- 환경변수 자격증명 자동 로그인 → 쿠키 로그인 → 브라우저 수동 로그인 순으로 폴백
- **같은 작품 판정은 BE 가 담당** — 여러 플랫폼에서 들어온 같은 작품은 한 작품에 플랫폼 · 링크 · 해시태그가 합쳐짐 (연령은 올리기만, 해시태그는 합집합)
- **JSONL 중복 제거** — `platform_work_id` 기준 in-memory dedup, 재실행 시 기존 파일 로드 후 덮어씌움

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

**검수 staging DB**

크롤러는 검수용 staging DB(`storix_staging_dev` · `storix_staging_prod`)만 씁니다. 운영 RDS 안의 별도 DB이고, 크롤러 계정 `storix_crawler`는 이 DB에만 권한이 있습니다(비밀번호: Parameter Store `/storix/crawler/STAGING_DB_PASSWORD`).

```bash
# SSM 터널 (storix-db-tunnel.sh 는 레포에 포함되지 않음 — 인프라 담당자에게 별도 전달받아 사용)
./storix-db-tunnel.sh          # 127.0.0.1:13306 유지
# .env: MYSQL_DATABASE_HOST=127.0.0.1 / PORT=13306 / USER=storix_crawler / PASSWORD=…
```

BE 대상(dev · prod)은 `STORIX_ENV` 또는 `--env` 로 고르고, 주소 · 내부 API 키 · staging DB 가 함께 바뀝니다(`review/env.py`, `.env.example` 참고).

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
python cli.py crawl --platform all --mode search_titles --titles-file titles.txt

# 작품명 리스트로 검색 크롤링 (쉼표 구분 직접 입력)
python cli.py crawl --platform all --mode search_titles --titles "나 혼자만 레벨업,재혼 황후"

# 플랫폼 5개를 동시에 (크롬이 플랫폼마다 따로 뜬다. initial · new_works · update_fields 도 같은 옵션)
python cli.py crawl --platform all --mode search_titles --titles-file titles.txt --parallel 5

# 네이버 웹소설(novel.naver.com) / 네이버 시리즈(series.naver.com) / 리디북스도 지원
python cli.py crawl --platform naver_novel  --mode search_titles --titles-file titles.txt
python cli.py crawl --platform naver_series --mode search_titles --titles "화산귀환,재벌집 막내아들"
python cli.py crawl --platform ridibooks    --mode search_titles --titles "갓겜하다 갓됨 갓뎀!"

# 커스텀 URL 크롤링 (네이버 웹툰 - 일일 연재+ 인기순, 상위 213개)
python cli.py crawl --mode custom_url --url "https://comic.naver.com/webtoon?tab=dailyPlus" --count 213

# 커스텀 URL 크롤링 (네이버 웹툰 - 완결 인기순, 상위 500개)
python cli.py crawl --mode custom_url --url "https://comic.naver.com/webtoon?tab=finish" --count 500

# 커스텀 URL 크롤링 (카카오페이지, 상위 300개)
python cli.py crawl --mode custom_url --url "https://www.kakaopage.com/content/..." --count 300

# 커스텀 URL 크롤링 (리디북스 단건 상세 — 특정 작품을 URL로 직접)
python cli.py crawl --mode custom_url --url "https://ridibooks.com/books/5131000001"

# 커스텀 URL 크롤링 (리디북스 카테고리 목록, 상위 200개)
python cli.py crawl --mode custom_url --url "https://ridibooks.com/category/bestsellers/1613?period=steady" --count 200
```

> **리디 단건 URL**을 이미 알고 있으면 `custom_url` 모드에 `/books/<id>` 상세 URL을 직접 넣어 크롤한 뒤 `stage load` → `stage import` 로 적재하세요(아래 **검수 · 적재**). 카테고리 목록 URL을 주면 목록을 수집해 각 상세를 크롤합니다. 작품명만 있으면 아래 `search_titles`를 쓰면 됩니다.

> **`search_titles` 모드 (임의 목록으로 특정 작품만 채우기)**
> - 지원 플랫폼: `naver_webtoon`, `naver_novel`, `naver_series`, `kakao_page`, `ridibooks`, `all`
> - `titles.txt`에 작품명을 줄바꿈으로 나열하면 각 플랫폼에서 제목 검색 → 상세 크롤 → JSONL 저장.
> - `titles.txt`는 레포에 포함되지 않습니다(`.gitignore`). 직접 만드세요.
> - 제목은 **`## 웹툰` / `## 웹소설` / `## 전체`** 섹션 헤더 아래에 둡니다. 섹션은 **찾을 작품 유형**이며, 헤더 없이 나온 제목은 스킵됩니다.
>   - `웹툰` → 네이버 웹툰·시리즈·카카오·리디에서 **웹툰판만** / `웹소설` → 네이버 웹소설·시리즈·카카오·리디에서 **웹소설판만**
>   - `전체` → 웹툰판·웹소설판을 각각 찾아 **있는 판을 다** 수집 (한쪽만 있으면 그것만)
>   - 시리즈·카카오·리디는 검색 결과에 웹툰·웹소설이 섞여 나오므로, 검색 화면의 유형 표기로 먼저 거르고 상세에서 판정한 `works_type`으로 한 번 더 확인합니다(최대 3개 후보)
>   - 웹소설은 연재판을 먼저 고르고, 연재판이 없을 때만 e북을 고릅니다. e북 연령은 보내지 않습니다(권마다 연령이 달라 웹소설 연령을 덮어쓸 수 있음)
>   - 외전·번외는 본편과 같은 작품이라 수집하지 않고, 19세 완전판·개정판은 BE가 다른 작품으로 보므로 본편과 함께 수집합니다
>   - 예전 `## 단행본` 섹션은 경고 후 웹소설로 찾습니다
> - 같은 섹션에 같은 제목이 여러 줄 있으면 **첫 줄만 쓰고 나머지는 제외**합니다(같은 작품 중복 크롤 방지). 단 `원룸 조교님` ⇄ `원룸조교님`처럼 표기가 다르면 별개로 봅니다 — 같은 작품 판정은 BE import 가 하고(라벨 · 띄어쓰기 · 기호를 뺀 제목 + 작가), 애매하면 `SUSPECTED_DUPLICATE` 로 검수 대기가 됩니다.
> - `--platform all`은 위 5개 플랫폼을 순회하며, **한 곳에서라도 찾으면** 해당 작품을 처리합니다.
> - 제목 매칭은 **완전일치 → 앞부분 일치 → 유사도 ≥90%** 순으로 시도합니다. `[완결]`·`[e북]` 같은 라벨을 떼고 정규화(공백·괄호·`·∙#` 제거) 후 비교합니다. 앞부분 일치는 뒤에 붙은 말이 부제·판본 구분자(` - `, `:`, `~`, `(`, `[`)로 시작할 때만 인정합니다(`레지나레나 - 용서받지 못한 그대에게`는 통과, `상수리나무 아래 4컷 만화`는 다른 작품). 임계값은 `BaseCrawler.TITLE_FUZZY_THRESHOLD`로 조정.
> - 네이버 웹툰 도전만화·베스트도전, 네이버 웹소설 베스트리그·챌린지리그는 **정식 계약 전 작품이라 수집하지 않습니다**(검수에서도 `PRE_CONTRACT_WORK`로 거절).
> - **리디북스**는 `ridibooks.com/search`에서 `/books/<id>`를 찾아 매칭합니다. 성인(19금) 작품은 **성인 인증된 계정으로 로그인**돼 있어야 검색 결과에 노출되니, 리디 세션(`sessions/ridibooks_cookies.pkl`)이 성인 인증 상태인지 확인하세요. 리디 연령은 책 데이터(성인) · "15세 · 12세 이용가 안내" 공지로 판정하고, 둘 다 없으면 전체연령가입니다. 성인 e북 표지가 가림 이미지면 책 ID 로 실제 표지를 받습니다.
> - **상세 수집은 HTTP 가 먼저**입니다. 네이버 웹툰은 작품 정보 API(`comic.naver.com/api/article/list/info`), 리디는 상세 HTML 안의 책 데이터(`__NEXT_DATA__`)로 받고, 못 받으면 브라우저로 엽니다. 브라우저는 이미지를 받지 않고(`eager` 로딩), 상세 100건마다 드라이버를 새로 띄웁니다.
> - **`--titles-file` 사용 시 크롤에 성공한 작품은 파일에서 자동 제거**되어, `titles.txt`에는 못 찾은 작품만 남습니다(재시도용). `--titles`(직접 입력)는 파일을 수정하지 않습니다.
> - BE import 는 빈 값을 덮어쓰지 않고, 연령은 올리기만 하며, 해시태그는 기존 태그에 더합니다.

**검수 · 적재 (`stage`)**

수집 결과(JSONL)는 바로 BE 로 가지 않고 staging 에서 검수를 거칩니다.

```bash
# 1) staging 적재 + 규칙 검사 (런 단위). --env 생략 시 STORIX_ENV(기본 dev)
python cli.py stage load --env dev --input 'output/2026-10-10/*_search_titles.jsonl' --source search_titles

# 2) 상태 확인
python cli.py stage stats --env dev

# 3) BE 반영 (보내기 전에 런별 건수를 보여주고 확인받음)
python cli.py stage import --env dev --run-id <런>
```

| 판정 | 의미 |
|---|---|
| `AUTO_PASS` | 바로 BE 로 보냄 |
| `NEEDS_REVIEW` | 사람이 확인 (장르 매핑 실패, 가림 표지, BE 중복 의심, 새 작품인데 연령 · 장르가 빔 등) |
| `REJECTED` | 보내지 않음 (작품명 · 작가 · 링크 없음, 정식 계약 전 작품 등) |

- 연령 · 장르가 비어도 막지 않습니다. 기존 작품에 붙으면 BE 가 빈 값을 무시하고, 새로 만들어야 하면 BE 가 거절해 그 건만 검수 대기가 됩니다(한 실행 안에서 마지막에 한 번 더 보내 다른 플랫폼 행이 만든 작품에 붙임)
- 검수 대기 처리는 검수 API(`uvicorn review.app:app --port 8200`)의 `GET /review/queue` · `POST /review/{id}/approve`(`overrides`, 기존 작품에 붙일 `target_works_id`) · `POST /review/{id}/reject` 로 합니다
- 수집 시각만 다르고 내용이 같으면 판정 · import 상태를 그대로 둬 BE 로 다시 보내지 않습니다

**작품별 수집 이력 · 링크 복구**

`stage load` 가 플랫폼 작품마다 수집 이력(staging DB `works_source`)을 남깁니다: 링크 · 연결된 BE 작품 ID · 제목 · 작가 · 검색어(지금까지 수집된 제목들) · 마지막 수집 · 성공 시각 · 상태 · 연속 실패 횟수. 같은 런의 수집 리포트(`<모드>_report.json`)에 있는 상세 수집 실패(`DETAIL_NOT_FOUND`)도 같이 기록하고, 3번 연달아 실패하면 그 작품을 검수 대기(`LINK_BROKEN`)로 돌립니다.

```bash
# 마지막 수집이 실패한 작품의 링크를 다시 찾는다 (크롬 사용)
#   옛 링크를 한 번 더 열고, 안 되면 저장된 검색어로 검색해 제목 · 작가 · 작품 유형이 맞는 후보를 고른다
python cli.py stage recover --env dev [--platform RIDIBOOKS] [--limit 50]
python cli.py stage load --env dev --input 'output/2026-10-10/*_recover.jsonl' --source recover
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
docker compose --profile scheduler up -d   # 수집 결과를 staging 에 적재까지 함. BE 반영은 stage import 로 사람이 실행

# DB만 실행 (로컬 개발용)
docker compose up db -d

# 크롤링 단발 실행
docker compose --profile crawl up
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

### CI

PR(`develop`/`main` 대상)과 `develop`/`main` 푸시 시 GitHub Actions(`.github/workflows/ci.yml`)가 돕니다.

- **Test**: Python 3.11, MySQL 8.0 서비스 컨테이너로 `pytest` 실행 (+ 전체 모듈 컴파일 확인)
- **Docker build**: 이미지가 빌드되는지만 확인 (푸시·배포 없음)

배포 워크플로우는 없습니다. 크롤링은 로컬(또는 `docker compose`)에서 수동 실행합니다.

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
├── cli.py                          # CLI 진입점 (login / crawl / stage 명령)
├── config.py                       # URL, DB, 경로, 로그인 자격증명 상수
├── requirements.txt
├── Dockerfile
├── docker-compose.yml              # 로컬용 (db / crawl / scheduler / api 프로필)
├── .env.example                    # 환경변수 템플릿
├── .github/workflows/
│   └── ci.yml                      # PR 검사 (pytest, docker build)
│
├── sessions/                       # 쿠키 파일 저장소 (.gitignore 처리됨)
│   ├── naver_cookies.pkl
│   ├── kakao_cookies.pkl
│   └── ridibooks_cookies.pkl
│
├── crawler/
│   ├── modes/
│   │   ├── initial.py              # 전체 작품 크롤링 (네이버/카카오 섹션별 + 리디북스 카테고리), 워커 2개
│   │   ├── new_works.py            # 신작 탭 크롤링
│   │   ├── update_fields.py        # source_url 목록으로 필드 재크롤링
│   │   ├── search_titles.py        # 작품명 리스트 → 플랫폼별 검색 → JSONL 저장, 성공분은 titles-file에서 자동 제거
│   │   └── custom_url.py           # 임의 목록/상세 URL 기반 크롤링 (--url, --count)
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
│   └── logger.py                   # 구조화 로거 팩토리
│
├── review/                         # 검수 파이프라인 (크롤러는 서비스 DB 에 직접 쓰지 않음)
│   ├── env.py                      # BE 대상 dev · prod (주소 · 내부 API 키 · staging DB)
│   ├── schema.sql · store.py       # staging DB (staging_run · works_staging · review_decision · works_source)
│   ├── catalog.py                  # BE enum 카탈로그 (장르 · 연령 · 유형 · 플랫폼)
│   ├── rules.py                    # Layer 1 규칙 검사 · Layer 1.5 런 이상 차단
│   ├── artists.py · landing.py     # 작가명 · 작품 링크 정규화
│   ├── service.py · app.py         # 적재 · 승인 · 거절, 검수 API (FastAPI)
│   └── importer.py · backend.py    # BE import API 호출 (X-Internal-Api-Key)
│
├── scheduler/
│   ├── jobs.py                     # 크롤링 잡 함수 (7개: initial 3 / new_works 2 / update_fields 2) — 결과는 staging 적재까지
│   └── runner.py                   # APScheduler BlockingScheduler 실행기
│
├── api/
│   └── session_api.py              # 세션(쿠키) 등록 API (FastAPI, :8100)
│
├── tools/
│   ├── register_session.py         # 로컬에서 로그인 → 쿠키 추출 → API 업로드 툴
│   └── build_exe.ps1               # 위 툴을 exe 로 빌드 (PyInstaller)
│
└── output/                         # 크롤링 산출물 (날짜별 JSONL, .gitignore 처리됨)
    └── YYYY-MM-DD/
        ├── naver_webtoon_initial.jsonl     # <플랫폼>_<모드>.jsonl. platform_work_id 기준 중복 제거, 재실행 시 덮어씌움
        ├── ridibooks_search_titles.jsonl   # 플랫폼마다 파일이 따로라 --parallel 로 동시에 돌려도 안 겹침
        ├── search_titles_report.json       # 수집 리포트 (플랫폼별 상태 코드)
        └── manual_review_queue.jsonl
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
