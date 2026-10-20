<h1>
<img src="https://github.com/user-attachments/assets/b65bf457-6754-4e0d-9d94-4511721b2b36" align=left width=100>
STORIX-BE-Crawler
</h1>

> 이야기에 대한 이야기로 모두가 연결되는 이 곳! 데이터로 취향을 연결하는 웹툰·웹소설 팬덤 플랫폼, STORIX

<br>

웹툰 · 웹소설 플랫폼에서 작품 정보를 모아 검수를 거쳐 STORIX BE 에 넣는 크롤러입니다.

```
수집 (crawl)          검수 (stage)                              반영
플랫폼 ──Selenium·HTTP──▶ JSONL ──▶ staging DB ──▶ 규칙 검사 ──▶ 사람 검수 ──▶ BE import API
                                          (Layer 1 · 1.5)     (Layer 3 매핑)
```

- **크롤러는 서비스 DB 에 직접 쓰지 않습니다.** 검수용 staging DB 에만 쓰고, BE 에는 import API 로만 보냅니다
- **같은 작품 판정은 BE 가 합니다.** 여러 플랫폼에서 들어온 같은 작품은 한 작품에 플랫폼 · 링크 · 해시태그가 합쳐집니다 (빈 값은 덮어쓰지 않고, 연령은 올리기만, 해시태그는 합집합)
- **사이트 규칙을 지킵니다.** 모든 요청 전에 robots.txt 를 확인해 막힌 경로면 멈추고, User-Agent 로 봇임을 밝힙니다 (`STORIXBot/1.0`, 자동화 표시도 숨기지 않음). 아이디 · 비밀번호 자동 입력은 하지 않습니다 (#131 · #135)
- 운영은 **서버(EC2)를 자정에 켜서 노션 작품 추가 요청만 처리하고 끕니다.** 상주 스케줄러는 없습니다

## 목차

1. [지원 플랫폼](#지원-플랫폼)
2. [빠른 시작](#빠른-시작)
3. [수집 `crawl`](#수집-crawl)
4. [검수 · 적재 `stage`](#검수--적재-stage)
5. [노션 작품 추가 요청 `requests`](#노션-작품-추가-요청-requests)
6. [로그인 · 세션](#로그인--세션)
7. [서버 운영](#서버-운영)
8. [개발](#개발)
9. [참고](#참고)

---

## 지원 플랫폼

| `--platform` | BE `platform` | 사이트 | 수집 방법 |
|---|---|---|---|
| `naver_webtoon` | `NAVER_WEBTOON` | comic.naver.com | 목록 · 제목 검색 · 링크 |
| `kakao_page` | `KAKAO_PAGE` | page.kakao.com | 목록 · 제목 검색 · 링크 |
| `lezhin` | `LEZHIN` | lezhin.com | 제목 검색 · 링크 |
| `mrblue` | `MRBLUE` | mrblue.com | 제목 검색 · 링크 |
| `ridibooks` | `RIDIBOOKS` | ridibooks.com | **링크로만** (봇의 검색을 막음) |
| `naver_series` | `NAVER_SERIES` | series.naver.com (웹소설 · 웹툰 단행본) | **링크로만** (robots.txt 가 검색을 막음) |
| - | `NAVER_NOVEL` | novel.naver.com | 수집 안 함 (robots.txt 가 사이트 전체를 막음) |
| - | `BOMTOON` | bomtoon.com | 수집 안 함 (robots.txt 가 `/api/` 를 막음) |

- 제목 검색의 `--platform all` 은 네이버 웹툰 · 카카오페이지를 돕니다
- 노션 요청 연재처가 봄툰 · 리디 · 시리즈뿐이면 다른 플랫폼에서 찾지 않고 '검토' 로 이유를 남깁니다
- 성인 작품은 **성인 인증된 로그인 세션**이 있어야 보입니다 ([로그인 · 세션](#로그인--세션))
- 네이버 도전만화 · 베스트도전은 정식 계약 전 작품이라 수집하지 않습니다

---

## 빠른 시작

```bash
# 1. 설치
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2. 환경변수 (.env.example 을 복사해 채운다)
cp .env.example .env

# 3. 로그인 세션 저장 (브라우저가 열린다)
python cli.py login --platform all

# 4. 수집 → 검수 적재 → BE 반영
python cli.py crawl --platform all --mode search_titles --titles "나 혼자만 레벨업,재혼 황후"
python cli.py stage load   --env dev --input 'output/<날짜>/*_search_titles.jsonl' --source search_titles
python cli.py stage import --env dev --run-id <런>
```

**staging DB 접속** — 운영 RDS 안의 별도 DB(`storix_staging_dev` · `storix_staging_prod`)이고, 크롤러 계정 `storix_crawler` 는 이 DB 에만 권한이 있습니다. 로컬에서는 SSM 터널로 붙습니다.

```bash
./storix-db-tunnel.sh   # 127.0.0.1:13306. 레포에 없음 — 인프라 담당자에게 받는다
# .env: MYSQL_DATABASE_HOST=127.0.0.1 / PORT=13306 / USER=storix_crawler / PASSWORD=…
```

**dev · prod 고르기** — `--env` 또는 `STORIX_ENV`(기본 dev). BE 주소 · 내부 API 키 · staging DB 가 함께 바뀝니다 (`review/env.py`).

---

## 수집 `crawl`

결과는 `output/<날짜>/<플랫폼>_<모드>.jsonl` 에 쌓입니다. 같은 작품(`platform_work_id`)은 한 줄로 합쳐지고, 다시 돌리면 기존 파일을 읽어 덮어씁니다.

| 모드 | 하는 일 | 예 |
|---|---|---|
| `initial` | 전체 작품 (섹션 · 카테고리별) | `--platform kakao_page --mode initial` |
| `new_works` | 신작 탭 | `--platform all --mode new_works` |
| `update_fields` | 이미 있는 링크로 다시 수집 | `--mode update_fields --input ./output/2026-05-09/` |
| `search_titles` | 작품명으로 검색해 수집 (네이버 웹툰 · 카카오 · 레진 · 미스터블루) | `--titles-file titles.txt` 또는 `--titles "A,B"` |
| `custom_url` | 목록 · 상세 URL 하나로 수집 (리디 · 시리즈는 이것으로) | `--url "<URL>" --count 200` |

```bash
# 여러 플랫폼을 동시에 (플랫폼마다 크롬이 따로 뜬다. 파일도 플랫폼마다 따로라 안 겹친다)
python cli.py crawl --platform all --mode search_titles --titles-file titles.txt --parallel 2

# URL 로: 네이버 웹툰 완결 인기순 상위 500 · 리디 카테고리 상위 200 · 리디 작품 하나
python cli.py crawl --mode custom_url --url "https://comic.naver.com/webtoon?tab=finish" --count 500
python cli.py crawl --mode custom_url --url "https://ridibooks.com/category/bestsellers/1613?period=steady" --count 200
python cli.py crawl --mode custom_url --url "https://ridibooks.com/books/5131000001"
```

<details>
<summary><b><code>search_titles</code> 자세히</b> — titles.txt 쓰는 법 · 제목 매칭 규칙</summary>

**titles.txt** (레포에 없음, 직접 만든다)

```
## 웹툰
나 혼자만 레벨업
## 웹소설
재혼 황후
## 전체
화산귀환
```

| 섹션 | 찾는 것 |
|---|---|
| `## 웹툰` | 웹툰판만 |
| `## 웹소설` | 웹소설판만 |
| `## 만화` | 출판 만화 (웹툰과 다른 작품이라 따로 찾는다) |
| `## 단행본` | 소설 e북일 수도 만화책일 수도 있어 웹소설 · 만화로 (노션 요청 '단행본' 과 같은 해석) |
| `## 전체` | 웹툰판 · 웹소설판을 각각 찾아 있는 판을 다 |

- 유형마다 그 유형이 있는 플랫폼에서만 찾습니다: 웹툰은 네이버 웹툰 · 카카오 · 레진 · 미스터블루, 웹소설 · 만화는 카카오 · 미스터블루

- 섹션 헤더 없이 나온 제목은 건너뜁니다
- `--titles-file` 을 쓰면 **수집에 성공한 제목은 파일에서 지워져** 못 찾은 것만 남습니다 (다시 돌리기용). `--titles` 는 파일을 건드리지 않습니다
- 같은 섹션에 같은 제목이 여러 줄이면 첫 줄만 씁니다. 표기가 다르면(`원룸 조교님` ⇄ `원룸조교님`) 따로 수집하고, 같은 작품인지는 BE 가 판정합니다 (애매하면 `SUSPECTED_DUPLICATE` 로 검수 대기)

**제목 매칭**
- 순서: 완전 일치 → 앞부분 일치 → 유사도 90% 이상 (`BaseCrawler.TITLE_FUZZY_THRESHOLD`)
- `[완결]` · `[e북]` 같은 라벨을 떼고, 공백 · 괄호 · `·∙#` 를 지운 뒤 비교합니다
- 앞부분 일치는 뒤에 붙은 말이 부제 · 판본 구분자(` - ` `:` `~` `(` `[`)로 시작할 때만 인정합니다 (`레지나레나 - 용서받지 못한 그대에게` 통과, `상수리나무 아래 4컷 만화` 는 다른 작품)
- 카카오는 검색 결과에 웹툰 · 웹소설이 섞여 나와, 검색 화면의 유형 표기로 거르고 상세에서 한 번 더 확인합니다 (후보 최대 3개)

**판본**
- 웹소설은 연재판을 먼저, 없을 때만 e북을 고릅니다. e북 연령은 기존 작품엔 보내지 않고(권마다 달라 웹소설 연령을 덮을 수 있음), 새로 만들 때만 씁니다
- 외전 · 번외는 본편과 같은 작품이라 수집하지 않고, 19세 완전판 · 개정판은 BE 가 다른 작품으로 봐서 같이 수집합니다

</details>

<details>
<summary><b>플랫폼별 메모</b> — 리디 연령 · 레진 · 미스터블루 · HTTP 우선 수집</summary>

- **상세 수집은 HTTP 가 먼저**입니다. 네이버 웹툰은 작품 정보 API, 리디는 상세 HTML 안의 책 데이터(`__NEXT_DATA__`)로 받고, 못 받으면 브라우저로 엽니다. 브라우저는 이미지를 받지 않고(`eager`), 상세 100건마다 새로 띄웁니다
- **리디북스** — `/books/<id>` 링크나 카테고리 목록 URL 로 수집합니다. 성인 작품 상세는 성인 인증된 세션에서만 열립니다. 연령은 책 데이터(성인) · "15세 · 12세 이용가 안내" 공지로 판정하고, 둘 다 없으면 전체연령가입니다. 성인 e북 표지가 가림 이미지면 책 ID 로 실제 표지를 받습니다
- **레진코믹스 · 미스터블루** — 로그인 쿠키로 브라우저 없이 받습니다 (레진: 페이지 안 Next.js 데이터, 미스터블루: 서버 HTML). 표지는 세로형(레진 600x800, 미스터블루 480x694)

</details>

---

## 검수 · 적재 `stage`

수집 결과는 바로 BE 로 가지 않고 staging DB 에서 검수를 거칩니다.

```bash
python cli.py stage load   --env dev --input 'output/2026-10-10/*_search_titles.jsonl' --source search_titles   # 적재 + 규칙 검사
python cli.py stage stats  --env dev                                                                          # 상태별 건수
python cli.py stage import --env dev --run-id <런>                                                             # BE 반영 (보내기 전 건수 확인)
```

| 판정 | 뜻 |
|---|---|
| `AUTO_PASS` | 바로 BE 로 보냄 |
| `NEEDS_REVIEW` | 사람이 확인 (장르 매핑 실패, 가림 표지, BE 중복 의심, 새 작품인데 연령 · 장르가 빔 등) |
| `REJECTED` | 보내지 않음 (작품명 · 작가 · 링크 없음, 정식 계약 전 작품 등) |

- 연령 · 장르가 비어도 막지 않습니다. 기존 작품에 붙으면 BE 가 빈 값을 무시하고, 새로 만들어야 하면 BE 가 거절해 그 건만 검수 대기가 됩니다
- 수집 시각만 다르고 내용이 같으면 다시 보내지 않습니다
- 검수는 검수 API(`uvicorn review.app:app --port 8200`)로 합니다: `GET /review/queue` · `POST /review/{id}/approve` (`overrides`, 기존 작품에 붙일 `target_works_id`) · `POST /review/{id}/reject`

### 판정 매핑 승격 (Layer 3)

검수에서 장르 · 연령 · 작품 유형을 고쳐 승인하면 `원문 → 결정값` 이 기록됩니다. 같은 원문에 같은 결론이 `REVIEW_PROMOTE_MIN`(기본 3)번 쌓이면 매핑으로 올라가, 다음 적재부터 자동으로 고쳐 통과시킵니다.

- `stage load` 때 자동으로 돌고, 결론이 갈리면 내려갑니다
- 바깥 검수 제안(`suggest:<도구>`)으로 승인된 판정은 0.5번으로 셉니다 (같은 실수가 규칙이 되지 않게)

```bash
python cli.py stage promote --env prod            # 지금 매핑 보기
python cli.py stage promote --env prod --min 5    # 기준을 바꿔 보기
```

### 수집 이력 · 링크 복구

`stage load` 가 작품마다 수집 이력(`works_source`)을 남깁니다. 상세 수집이 **3번 연달아 실패하면** 그 작품을 검수 대기(`LINK_BROKEN`)로 돌립니다.

```bash
# 옛 링크를 다시 열고, 안 되면 저장된 검색어로 다시 찾는다 (크롬 사용)
python cli.py stage recover --env dev [--platform RIDIBOOKS] [--limit 50]
python cli.py stage load    --env dev --input 'output/<날짜>/*_recover.jsonl' --source recover
```

검수 API: `GET /sources?status=broken` (복구 대상) · `GET /sources/{platform}/{platform_work_id}` (작품 하나의 이력)

### 검수 대기를 바깥 도구로 정리하기

```bash
python cli.py stage queue   --env prod --all [--limit 200] --output queue.json   # 검수 대기 내보내기 (enum 값 목록 포함)
python cli.py stage suggest --env prod --input suggestions.json --by claude       # 제안 기록
python cli.py stage apply   --env prod --all --import                             # 제안 반영 → 이번에 승인한 건만 BE 로
```

- 제안 형식: `[{"staging_id", "action": "approve" | "reject" | "skip", "overrides", "confidence", "reason"}]`
- 승인은 고친 값으로 규칙 검사를 다시 통과해야 하고, 작품명 · 플랫폼 · 링크는 제안으로 고칠 수 없습니다

---

## 노션 작품 추가 요청 `requests`

랜딩 폼 '작품 추가 요청' DB 에서 아직 처리 안 된 요청을 읽어 **수집 → 검수 적재 → BE 반영 → 노션 표시** 까지 합니다. 서버 야간 실행이 이 명령입니다.

| 결과 | 노션 표시 |
|---|---|
| BE 에 들어감 (새로 생성 · 기존 갱신) | '추가 여부' 체크 |
| 못 찾음 · 검수 대기 · 중복 의심 | '적재 검토 중' 체크 + '메모' 에 이유 |

```bash
python cli.py requests --env prod --dry-run   # 수집 · 검수 적재까지만 (BE · 노션은 안 건드림) + 상태 파일
python cli.py requests --env prod --resume    # dry-run 결과를 이어받아 BE 반영 → 노션 표시 (다시 수집 안 함)
python cli.py requests --env prod             # 처음부터 끝까지
```

- dry-run 과 resume 사이에 검수 대기를 바깥 도구로 정리할 수 있습니다: `stage queue --run-id <런>` → `stage suggest` → `requests --resume`
- 필요한 값: `.env` 의 `NOTION_TOKEN` · `NOTION_REQUEST_DB_ID`, 노션 통합을 DB 페이지에 연결(Connections). 시작 요청 ID 는 `NOTION_REQUEST_MIN_ID`(기본 441)

---

## 로그인 · 세션

| 순서 | 방법 |
|---|---|
| 1 | 저장된 쿠키 `sessions/*.pkl` (커밋 안 됨) |
| 2 | 쿠키가 없거나 만료면 브라우저 창이 열리고, 직접 로그인하면 완료를 자동 감지 (최대 3분) |

```bash
python cli.py login --platform kakao_page   # 플랫폼 하나 (naver_webtoon · kakao_page · ridibooks · lezhin · mrblue)
python cli.py login --platform all          # 전부 차례로 (lezhin · mrblue 는 빠짐)
```

- 아이디 · 비밀번호를 환경변수로 넣어 자동 입력하는 방식은 없앴습니다. 로그인 화면은 robots.txt 가 막고, 네이버 약관은 자동화 수단 로그인을 금지합니다 (#131)
- 수집 중 세션이 만료되면 드라이버를 다시 띄우고 다시 로그인해 이어갑니다
- **카카오페이지 첫 로그인**: 로그인 뒤 성인 웹툰을 하나 눌러 연령 확인까지 끝내야 성인 작품이 수집됩니다
- **레진 · 미스터블루**: 성인 인증된 세션을 저장해 두면 그 뒤로는 브라우저 없이 받습니다
- Docker(서버)는 화면이 없어 첫 로그인은 로컬에서 합니다

---

## 서버 운영

### 야간 실행

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

### 슬랙 알림

| 알림 | 언제 |
|---|---|
| 세션 만료 · 차단 · 수집 실패 · 예외로 멈춤 | 야간 실행 중 (`AUTH_EXPIRED` · `RATE_LIMITED` · `PARSING_FAILED`) |
| 주간 작품 추가 | 월요일 자정 실행 때 지난주 새로 만든 작품. 원글엔 건수만, 작품명은 스레드 답글로 (없으면 안 보냄). 직접: `stage weekly --env prod [--date YYYY-MM-DD] [--send]` |

- `SLACK_BOT_TOKEN` · `SLACK_CHANNEL_ID`(BE 와 같은 봇)가 없으면(로컬) 보내지 않습니다
- 스레드 답글의 결과 이모지(🔐 · ✅ · ⚠️ · ❌)를 원글에도 달아 목록만 봐도 결과가 보입니다

### 세션이 만료됐을 때

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

### 설정 (Parameter Store `/storix/crawler/`)

파라미터 이름이 곧 환경변수 이름입니다.

| 이름 | 값 |
|---|---|
| `MYSQL_DATABASE_HOST` · `MYSQL_DATABASE_PORT` | staging RDS 주소 · 3306 |
| `STAGING_DB_USERNAME` · `STAGING_DB_PASSWORD` | staging 계정 (`MYSQL_DATABASE_USER` · `PASSWORD` 로도 넘김) |
| `NOTION_TOKEN` · `NOTION_REQUEST_DB_ID` | 노션 작품 요청 DB |

BE 와 같이 쓰는 값은 복사하지 않고 `/storix/prod/env/` 에서 읽습니다: `INTERNAL_API_KEY`(→ `STORIX_PROD_INTERNAL_API_KEY`) · `SLACK_BOT_TOKEN` · `SLACK_CHANNEL_ID`

### 처음 설치 · 점검

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

<details>
<summary><b>세션 등록 API · 세션 등록 툴</b> (화면 없는 서버에 쿠키를 넣는 예전 방식)</summary>

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

</details>

---

## 개발

### 브랜치 · 배포

BE 와 같은 Release 방식입니다. `develop` 에 기능 PR 을 모은 뒤 `release/vX.Y.Z` → `main` Release PR 을 머지합니다.

| 워크플로 | 언제 | 하는 일 |
|---|---|---|
| CI (`ci.yml`) | `develop` · `main` 대상 PR · 푸시 | Python 3.11 + MySQL 8.0 으로 `pytest`, 전체 모듈 컴파일, Docker 빌드 확인 |
| CD (`cd.yml`) | `main` 푸시 | 이미지를 ECR `storix-crawler`(`:latest` · `:<커밋>`)에 올림. 서버는 다음 자정에 받음 |

### staging DB 구조 바꾸기 (Alembic)

구조는 `review/models.py` 에 두고 변경은 Alembic 리비전(`review/migrations/versions`)으로 남깁니다 (BE 의 JPA + Flyway 와 같은 방식).

```bash
STORIX_ENV=dev alembic revision --autogenerate -m "works_source 에 memo 추가"   # 생성 파일은 꼭 검토 (이름 변경 · 데이터 이전은 모름)
STORIX_ENV=dev alembic upgrade head                                              # stage · 검수 서버 시작 때 자동으로도 돈다
STORIX_ENV=prod alembic current
```

- 모델만 고치고 리비전을 안 만들면 `tests/test_migrations.py` 가 실패합니다
- 접속 정보는 `review/env.py` · `config.py` 를 따릅니다 (`alembic.ini` 에 주소를 적지 않음)

### 로컬 실행 메모

- 로컬은 Docker 대신 `python cli.py ...` 로 실행합니다 (로그인 창이 필요해서)
- Apple Silicon 맥에선 Dockerfile 의 `--platform=linux/amd64` 로 빌드됩니다
- PyCharm: Settings → Python Interpreter → Add Interpreter → Virtualenv → 프로젝트 루트

---

## 참고

### 폴더 구조

```
cli.py                  CLI 진입점 (login · keepalive · crawl · stage · requests)
config.py               URL · DB · 경로 상수
crawler/
  modes/                initial · new_works · update_fields · search_titles · custom_url · recover · requests
  output/jsonl_writer.py  JSONL 기록 (platform_work_id 로 중복 제거, 작가명 정규화)
  notify.py             슬랙 알림
  remote_login.py       폰 원격 로그인 (noVNC + Cloudflare 임시 터널)
modules/crawler/        플랫폼별 크롤러 (base_crawler · naver · naver_series · kakao · ridibooks · lezhin · mrblue · …), robots.py (robots.txt 검사)
review/                 검수 파이프라인
  env.py                dev · prod 대상
  models.py · store.py  staging DB 구조 · 쿼리
  migrations/           Alembic 리비전
  catalog.py            BE enum 카탈로그 (장르 · 연령 · 유형 · 플랫폼)
  rules.py              Layer 1 규칙 검사 · Layer 1.5 런 이상 차단
  service.py · app.py   적재 · 승인 · 거절, 검수 API (FastAPI)
  importer.py · backend.py  BE import API 호출
deploy/                 서버 야간 실행 · 쿠키 올리기 · 슬랙 버튼 Lambda · systemd 유닛
api/session_api.py      세션 등록 API
tools/                  세션 등록 툴 (exe 빌드)
sessions/               쿠키 (커밋 안 됨)
output/<날짜>/          수집 결과 JSONL · 수집 리포트 (커밋 안 됨)
```

### 출력 JSONL (schema_version 2.0)

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
