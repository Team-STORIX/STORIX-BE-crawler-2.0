# STORIX-BE-Crawler 2.0

네이버 웹툰 / 카카오페이지 크롤러
Selenium 병렬 워커 → JSONL 산출물 → DB 배치 적재 파이프라인을 구축

---

## 완료된 작업

### Phase A — 2.0 파이프라인 기반
- CLI 진입점 (`cli.py`)
- JSONL 산출물 작성기 (`crawler/output/jsonl_writer.py`)
- initial 크롤링 모드 (`crawler/modes/initial.py`)
- Docker 환경 (`Dockerfile`, `docker-compose.yml`)

### Phase B — 추가 크롤링 모드
- 신작 탭 크롤링 (`crawler/modes/new_works.py`)
- 기존 JSONL 기반 필드 재크롤링 (`crawler/modes/update_fields.py`)

### Phase C — 배치 적재
- 스키마 검증기 (`batch/validator.py`)
- 폴백 처리기 (`batch/fallback.py`) — 누락 필드 복구, 수동 검수 큐 기록
- JSONL → DB 임포터 (`batch/importer.py`) — `--watch` 감시 모드 포함

### Phase D — 스케줄러
- APScheduler 기반 자동 실행 (`scheduler/runner.py`, `scheduler/jobs.py`)

### Phase E — 모니터링
- 세션 만료 감지 (`monitor/session_watcher.py`)
- 플랫폼별 실행 이력 로깅 (`monitor/platform_status.py`)

### 기존 코드 개선 (Phase 1–4)
- `WebtoonCrawler` import 오류 수정 (`modules/crawler/__init__.py`)
- 네이버/카카오 쿠키 자동 로그인 (`login_with_cookies()`)
- 병렬 워커 풀 (`ThreadPoolExecutor` + `queue.Queue`)
- `crawl_detail_with_retry()` — 최대 3회 재시도
- DB ON DUPLICATE KEY 장르/작가 우선순위 보호 (`modules/db_handler.py`)
- 구조화 로깅 (`modules/logger.py`)

---

## 실행 방법

### 로컬 (Python 직접)

```bash
pip install -r requirements.txt
```

**.env 설정**
```
MYSQL_DATABASE_HOST=localhost
MYSQL_DATABASE_USER=root
MYSQL_DATABASE_PASSWORD=비밀번호
MYSQL_DATABASE_NAME=storix
```

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

### Docker

```bash
# DB만 실행 (로컬 개발용)
docker compose up db

# 크롤링 실행
docker compose --profile crawl up

# 배치 적재 실행
docker compose --profile batch up

# 스케줄러 실행
docker compose --profile scheduler up
```

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
│
├── crawler/
│   ├── modes/
│   │   ├── initial.py              # 전체 작품 크롤링 (장르 + 매일+ + 완결)
│   │   ├── new_works.py            # 신작 탭 크롤링
│   │   └── update_fields.py        # source_url 목록으로 필드 재크롤링
│   └── output/
│       └── jsonl_writer.py         # 크롤 결과를 JSONL 파일로 기록, artist_name 정규화
│
├── modules/
│   ├── crawler/
│   │   ├── base_crawler.py         # WebDriver 초기화, crawl_detail_with_retry()
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
└── output/                         # 크롤링 산출물 (날짜별 JSONL)
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
