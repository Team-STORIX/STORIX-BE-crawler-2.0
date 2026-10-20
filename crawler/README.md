# 수집 상세

[← README](../README.md)

## 모드

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

## 제목 검색 `search_titles`

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


## 플랫폼별 메모

- **상세 수집은 HTTP 가 먼저**입니다. 네이버 웹툰은 작품 정보 API, 리디는 상세 HTML 안의 책 데이터(`__NEXT_DATA__`)로 받고, 못 받으면 브라우저로 엽니다. 브라우저는 이미지를 받지 않고(`eager`), 상세 100건마다 새로 띄웁니다
- **리디북스** — `/books/<id>` 링크나 카테고리 목록 URL 로 수집합니다. 성인 작품 상세는 성인 인증된 세션에서만 열립니다. 연령은 책 데이터(성인) · "15세 · 12세 이용가 안내" 공지로 판정하고, 둘 다 없으면 전체연령가입니다. 성인 e북 표지가 가림 이미지면 책 ID 로 실제 표지를 받습니다
- **레진코믹스 · 미스터블루** — 로그인 쿠키로 브라우저 없이 받습니다 (레진: 페이지 안 Next.js 데이터, 미스터블루: 서버 HTML). 표지는 세로형(레진 600x800, 미스터블루 480x694)


## 출력 JSONL (schema_version 2.0)

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
