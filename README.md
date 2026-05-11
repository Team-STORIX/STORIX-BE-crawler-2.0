# STORIX-BE-Crawler 2.0

네이버 웹툰 / 카카오페이지 크롤러
Selenium 병렬 워커 → JSONL 산출물 → DB 배치 적재 파이프라인

---

## 주요 기능

- CLI 진입점 (`cli.py`) — `crawl` / `batch` 명령
- JSONL 산출물 작성기 (`crawler/output/jsonl_writer.py`)
- 크롤링 모드: initial (전체), new_works (신작), update_fields (필드 갱신)
- 배치 적재: 스키마 검증 → 폴백 복구 → DB INSERT (`batch/`)
- APScheduler 기반 자동 실행 (`scheduler/`)
- 모니터링: 세션 만료 감지, 플랫폼별 실행 이력 (`monitor/`)
- 병렬 워커 풀 (`ThreadPoolExecutor` + `queue.Queue`, 워커 2개)
- 쿠키 자동 로그인 + 세션 만료 시 GUI 팝업 재로그인

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
# 전체 initial 크롤링 (네이버 + 카카오)
python cli.py crawl --platform all --mode initial

# 신작 크롤링
python cli.py crawl --platform naver_webtoon --mode new_works

# 특정 플랫폼만
python cli.py crawl --platform kakao_page --mode initial

# 필드 갱신 (기존 JSONL 재크롤링)
python cli.py crawl --platform all --mode update_fields --input ./output/2026-05-09/
```

**배치 적재**
```bash
# 특정 날짜 폴더 전체 DB 적재
python cli.py batch import --input ./output/2026-05-09/

# 감시 모드 (새 파일 생성 시 자동 적재)
python cli.py batch import --input ./output/ --watch

# 수동 검수 큐 확인
python cli.py batch review --input ./output/2026-05-09/manual_review_queue.jsonl
```

**스케줄러**
```bash
python -m scheduler.runner
```

---

### 로그인 및 세션 관리

최초 실행 시 브라우저가 열리며 수동 로그인이 필요합니다. 로그인 완료 후 팝업의 [확인]을 누르면 쿠키가 `sessions/` 폴더에 저장되고, 이후 실행부터는 자동 로그인됩니다.

**카카오페이지 최초 로그인 시 주의사항**
1. 브라우저에서 카카오 로그인을 완료하세요.
2. 성인 웹툰을 하나 클릭해 '연령 확인'이 뜨면 인증을 완료하세요.
3. 모든 준비가 끝나면 팝업의 [확인]을 누르세요.

크롤링 중 세션이 만료되면 자동으로 드라이버를 재시작하고, 쿠키 로그인도 실패할 경우 팝업을 띄워 재로그인을 기다린 뒤 크롤링을 재개합니다.

쿠키 파일(`sessions/*.pkl`)은 `.gitignore`에 등록되어 있어 커밋되지 않습니다.

---

### Docker

Docker는 서버 배포용입니다. 로컬 개발에는 Python 직접 실행을 권장합니다.
크롤러를 Docker로 실행하려면 먼저 로컬에서 로그인해 `sessions/*.pkl`을 생성한 뒤, 해당 파일을 컨테이너에 마운트하세요.

```bash
# DB만 실행 (로컬 개발용)
docker compose up db -d

# 크롤링 실행
docker compose --profile crawl up

# 배치 적재 실행
docker compose --profile batch up

# 스케줄러 실행
docker compose --profile scheduler up
```

> Apple Silicon(M1/M2/M3) Mac에서는 Dockerfile에 `--platform=linux/amd64`가 설정되어 있습니다.
> Chrome + MySQL을 동시에 실행하면 메모리 부족이 발생할 수 있으니, 로컬에서는 `docker compose up db`만 사용하고 크롤러는 Python으로 직접 실행하세요.

---

## 스케줄 테이블 (기본값, Asia/Seoul)

| 잡 | 주기 | 시간 |
|----|------|------|
| initial (네이버) | 매월 1일 | 03:00 |
| initial (카카오) | 매월 1일 | 06:00 |
| new_works (네이버) | 매일 | 09:00 |
| new_works (카카오) | 매일 | 09:30 |
| update_fields (네이버) | 매주 월요일 | 02:00 |
| update_fields (카카오) | 매주 월요일 | 02:30 |

환경변수로 오버라이드 가능: `SCHED_NEW_WORKS_HOUR`, `SCHED_INITIAL_NAVER_HOUR` 등

---

## 파일 구조 및 역할

```
.
├── cli.py                          # CLI 진입점 (crawl / batch 명령)
├── config.py                       # URL, DB, 경로 상수
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
│   │   ├── initial.py              # 전체 작품 크롤링 (장르 + 매일+ + 완결), 워커 2개
│   │   ├── new_works.py            # 신작 탭 크롤링
│   │   └── update_fields.py        # source_url 목록으로 필드 재크롤링
│   └── output/
│       └── jsonl_writer.py         # 크롤 결과를 JSONL 파일로 기록, artist_name 정규화
│
├── modules/
│   ├── crawler/
│   │   ├── base_crawler.py         # WebDriver 초기화, crawl_detail_with_retry(), 세션 복구
│   │   ├── naver_crawler.py        # 네이버 웹툰 크롤러 (로그인, URL 수집, 상세 파싱)
│   │   └── kakao_crawler.py        # 카카오페이지 크롤러
│   ├── db_handler.py               # MySQL 연결, save_one_row(), parse_artists()
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
└── output/                         # 크롤링 산출물 (날짜별 JSONL, .gitignore 처리됨)
    └── YYYY-MM-DD/
        ├── naver_webtoon_initial_HHMMSS.jsonl
        ├── kakao_page_initial_HHMMSS.jsonl
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
