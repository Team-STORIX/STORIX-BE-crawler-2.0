# 검수 · 적재

[← README](../README.md)

수집 결과(JSONL)는 바로 BE 로 가지 않고 staging DB 에서 검수를 거칩니다. 크롤러는 서비스 DB 에 직접 쓰지 않습니다.

## 기본 흐름

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

## 판정 매핑 승격 (Layer 3)

검수에서 장르 · 연령 · 작품 유형을 고쳐 승인하면 `원문 → 결정값` 이 기록됩니다. 같은 원문에 같은 결론이 `REVIEW_PROMOTE_MIN`(기본 3)번 쌓이면 매핑으로 올라가, 다음 적재부터 자동으로 고쳐 통과시킵니다.

- `stage load` 때 자동으로 돌고, 결론이 갈리면 내려갑니다
- 바깥 검수 제안(`suggest:<도구>`)으로 승인된 판정은 0.5번으로 셉니다 (같은 실수가 규칙이 되지 않게)

```bash
python cli.py stage promote --env prod            # 지금 매핑 보기
python cli.py stage promote --env prod --min 5    # 기준을 바꿔 보기
```

## 수집 이력 · 링크 복구

`stage load` 가 작품마다 수집 이력(`works_source`)을 남깁니다. 상세 수집이 **3번 연달아 실패하면** 그 작품을 검수 대기(`LINK_BROKEN`)로 돌립니다.

```bash
# 옛 링크를 다시 열고, 안 되면 저장된 검색어로 다시 찾는다 (크롬 사용)
python cli.py stage recover --env dev [--platform RIDIBOOKS] [--limit 50]
python cli.py stage load    --env dev --input 'output/<날짜>/*_recover.jsonl' --source recover
```

검수 API: `GET /sources?status=broken` (복구 대상) · `GET /sources/{platform}/{platform_work_id}` (작품 하나의 이력)

## 검수 대기를 바깥 도구로 정리하기

```bash
python cli.py stage queue   --env prod --all [--limit 200] --output queue.json   # 검수 대기 내보내기 (enum 값 목록 포함)
python cli.py stage suggest --env prod --input suggestions.json --by claude       # 제안 기록
python cli.py stage apply   --env prod --all --import                             # 제안 반영 → 이번에 승인한 건만 BE 로
```

- 제안 형식: `[{"staging_id", "action": "approve" | "reject" | "skip", "overrides", "confidence", "reason"}]`
- 승인은 고친 값으로 규칙 검사를 다시 통과해야 하고, 작품명 · 플랫폼 · 링크는 제안으로 고칠 수 없습니다


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


## staging DB 구조 바꾸기 (Alembic)

구조는 `review/models.py` 에 두고 변경은 Alembic 리비전(`review/migrations/versions`)으로 남깁니다 (BE 의 JPA + Flyway 와 같은 방식).

```bash
STORIX_ENV=dev alembic revision --autogenerate -m "works_source 에 memo 추가"   # 생성 파일은 꼭 검토 (이름 변경 · 데이터 이전은 모름)
STORIX_ENV=dev alembic upgrade head                                              # stage · 검수 서버 시작 때 자동으로도 돈다
STORIX_ENV=prod alembic current
```

- 모델만 고치고 리비전을 안 만들면 `tests/test_migrations.py` 가 실패합니다
- 접속 정보는 `review/env.py` · `config.py` 를 따릅니다 (`alembic.ini` 에 주소를 적지 않음)
