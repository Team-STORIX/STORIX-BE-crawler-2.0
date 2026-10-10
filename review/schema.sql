-- 검수 파이프라인 staging 스키마. 서비스 DB(storix)와 분리된 DB(STAGING_DATABASE_NAME)에 둔다.
-- Works 테이블은 BE 만 쓰고, 여기 있는 테이블은 검수 서비스만 쓴다.
--
-- raw 컬럼은 전부 TEXT 다. 타입을 걸면 넣는 순간 터져서 "뭐가 틀렸는지" 기록할 기회가 없다.
-- 모든 문장은 IF NOT EXISTS 라 기동할 때마다 실행해도 된다.

CREATE TABLE IF NOT EXISTS staging_run (
    run_id        VARCHAR(64)  NOT NULL PRIMARY KEY,
    source        VARCHAR(100) NOT NULL,             -- 직전 런과 건수를 비교할 단위 (예: naver_webtoon_initial)
    source_file   TEXT         NULL,
    item_count    INT          NOT NULL DEFAULT 0,
    held          BOOLEAN      NOT NULL DEFAULT FALSE, -- Layer 1.5 서킷 브레이커가 걸면 TRUE. import 대상에서 빠진다
    hold_reasons  JSON         NULL,
    released_by   VARCHAR(100) NULL,
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_source_created (source, created_at)
);

CREATE TABLE IF NOT EXISTS works_staging (
    id                  BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
    run_id              VARCHAR(64)  NOT NULL,
    source_url          VARCHAR(768) NOT NULL,

    -- 크롤러가 뱉은 값 그대로
    platform            TEXT NULL,
    works_name          TEXT NULL,
    artist_name         TEXT NULL,
    author              TEXT NULL,
    illustrator         TEXT NULL,
    original_author     TEXT NULL,
    age_classification  TEXT NULL,
    genre               TEXT NULL,
    works_type          TEXT NULL,
    description         TEXT NULL,
    thumbnail_url       TEXT NULL,
    hashtags            JSON NULL,
    raw                 JSON NOT NULL,

    -- 검수 결과
    status              VARCHAR(20)  NOT NULL DEFAULT 'PENDING',
    normalized          JSON         NULL,   -- enum 을 BE name 으로 매핑한 값. import 는 이걸 보낸다
    violations          JSON         NULL,
    llm_verdict         JSON         NULL,
    confidence          DECIMAL(4,3) NULL,
    reviewed_by         VARCHAR(100) NULL,
    reviewed_at         DATETIME     NULL,
    imported_works_id   BIGINT       NULL,
    import_error        TEXT         NULL,

    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uk_source_url (source_url),
    KEY idx_status (status),
    KEY idx_run (run_id)
);

-- 사람 판정 기록. 같은 (field, raw_value) 가 N회 같은 결론이면 Layer 3 에서 매핑 테이블로 승격한다
CREATE TABLE IF NOT EXISTS review_decision (
    id             BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
    staging_id     BIGINT       NOT NULL,
    field          VARCHAR(50)  NOT NULL,
    raw_value      TEXT         NULL,
    decided_value  TEXT         NULL,
    decided_by     VARCHAR(100) NULL,
    decided_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_field (field),
    KEY idx_staging (staging_id)
);

-- 작품별 수집 이력 (#28). 플랫폼 작품 하나당 한 행. 어느 링크로 언제 수집됐고 실패했는지, BE 어느 작품에 붙었는지.
-- 수집 상태 · 실패 횟수는 크롤러 운영 정보라 서비스 DB(works_platform) 가 아니라 여기 둔다
CREATE TABLE IF NOT EXISTS works_source (
    id                BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
    platform          VARCHAR(50)  NOT NULL,              -- BE Platform enum name (링크에서 판정)
    platform_work_id  VARCHAR(100) NOT NULL,
    source_url        VARCHAR(768) NOT NULL,              -- 대표 링크 (review/landing.py)
    works_id          BIGINT       NULL,                  -- import 결과로 연결된 BE 작품
    title_snapshot    VARCHAR(255) NULL,
    artist_snapshot   VARCHAR(255) NULL,
    works_type        VARCHAR(20)  NULL,
    search_keywords   JSON         NULL,                  -- 링크가 깨졌을 때 다시 찾을 검색어 (지금까지 수집된 제목들)
    last_crawled_at   DATETIME     NULL,
    last_success_at   DATETIME     NULL,
    crawl_status      VARCHAR(30)  NULL,                  -- crawler/report.py 상태 코드 · RELINKED
    crawl_fail_count  INT          NOT NULL DEFAULT 0,
    last_error        TEXT         NULL,
    relinked_to       VARCHAR(768) NULL,                  -- 링크 복구로 찾은 새 링크
    fingerprint_hash  CHAR(64)     NULL,                  -- 수집 내용 해시. 같으면 다시 보내지 않는다
    created_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_platform_work (platform, platform_work_id),
    KEY idx_works (works_id),
    KEY idx_status (crawl_status)
);
