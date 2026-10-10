"""staging DB 테이블 구조 (#52). 서비스 DB(storix)와 분리된 DB(storix_staging_{env})에 둔다.

BE 의 JPA 엔티티처럼 구조는 여기서만 정의하고, 변경은 Alembic 리비전(review/migrations/versions)으로 남긴다.
쿼리는 review/store.py 가 SQL 로 직접 쓴다 — 이 모델은 구조 관리용이다.

구조를 바꿀 때
    1) 이 파일의 모델을 고친다
    2) alembic revision --autogenerate -m "설명"   → versions/ 에 리비전 생성
    3) 생성된 리비전을 검토해 커밋한다 (자동 생성은 이름 변경 · 데이터 이전을 모른다)
    적용은 stage 명령 · 검수 서버가 시작할 때 ensure_schema() 가 한다 (alembic upgrade head)

Works 테이블은 BE 만 쓰고, 여기 있는 테이블은 검수 서비스만 쓴다.
raw 컬럼은 전부 TEXT 다. 타입을 걸면 넣는 순간 터져서 "뭐가 틀렸는지" 기록할 기회가 없다.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CHAR, JSON, BigInteger, Boolean, DateTime, Index, Integer, Numeric, String, Text, UniqueConstraint, text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

CREATED_AT = text('CURRENT_TIMESTAMP')
UPDATED_AT = text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP')


class Base(DeclarativeBase):
    pass


class StagingRun(Base):
    __tablename__ = 'staging_run'
    __table_args__ = (Index('idx_source_created', 'source', 'created_at'),)

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # 직전 런과 건수를 비교할 단위 (예: naver_webtoon_initial)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    source_file: Mapped[str | None] = mapped_column(Text)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('0'))
    # Layer 1.5 서킷 브레이커가 걸면 TRUE. import 대상에서 빠진다
    held: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('0'))
    hold_reasons: Mapped[list | None] = mapped_column(JSON)
    released_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=CREATED_AT)


class WorksStaging(Base):
    __tablename__ = 'works_staging'
    __table_args__ = (
        UniqueConstraint('source_url', name='uk_source_url'),
        Index('idx_status', 'status'),
        Index('idx_run', 'run_id'),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[str] = mapped_column(String(768), nullable=False)

    # 크롤러가 뱉은 값 그대로
    platform: Mapped[str | None] = mapped_column(Text)
    works_name: Mapped[str | None] = mapped_column(Text)
    artist_name: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(Text)
    illustrator: Mapped[str | None] = mapped_column(Text)
    original_author: Mapped[str | None] = mapped_column(Text)
    age_classification: Mapped[str | None] = mapped_column(Text)
    genre: Mapped[str | None] = mapped_column(Text)
    works_type: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    hashtags: Mapped[list | None] = mapped_column(JSON)
    raw: Mapped[dict] = mapped_column(JSON, nullable=False)

    # 검수 결과
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'PENDING'"))
    # enum 을 BE name 으로 매핑한 값. import 는 이걸 보낸다
    normalized: Mapped[dict | None] = mapped_column(JSON)
    violations: Mapped[list | None] = mapped_column(JSON)
    llm_verdict: Mapped[dict | None] = mapped_column(JSON)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    reviewed_by: Mapped[str | None] = mapped_column(String(100))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    imported_works_id: Mapped[int | None] = mapped_column(BigInteger)
    import_error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=CREATED_AT)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=UPDATED_AT)


class ReviewDecision(Base):
    """사람 판정 기록. 같은 (field, raw_value) 가 N회 같은 결론이면 Layer 3 에서 매핑 테이블로 승격한다."""
    __tablename__ = 'review_decision'
    __table_args__ = (
        Index('idx_field', 'field'),
        Index('idx_staging', 'staging_id'),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    staging_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    field: Mapped[str] = mapped_column(String(50), nullable=False)
    raw_value: Mapped[str | None] = mapped_column(Text)
    decided_value: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str | None] = mapped_column(String(100))
    decided_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=CREATED_AT)


class WorksSource(Base):
    """작품별 수집 이력 (#28). 플랫폼 작품 하나당 한 행. 어느 링크로 언제 수집됐고 실패했는지, BE 어느 작품에 붙었는지.
    수집 상태 · 실패 횟수는 크롤러 운영 정보라 서비스 DB(works_platform) 가 아니라 여기 둔다."""
    __tablename__ = 'works_source'
    __table_args__ = (
        UniqueConstraint('platform', 'platform_work_id', name='uk_platform_work'),
        Index('idx_works', 'works_id'),
        Index('idx_status', 'crawl_status'),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)          # BE Platform enum name (링크에서 판정)
    platform_work_id: Mapped[str] = mapped_column(String(100), nullable=False)
    source_url: Mapped[str] = mapped_column(String(768), nullable=False)       # 대표 링크 (review/landing.py)
    works_id: Mapped[int | None] = mapped_column(BigInteger)                   # import 결과로 연결된 BE 작품
    title_snapshot: Mapped[str | None] = mapped_column(String(255))
    artist_snapshot: Mapped[str | None] = mapped_column(String(255))
    works_type: Mapped[str | None] = mapped_column(String(20))
    search_keywords: Mapped[list | None] = mapped_column(JSON)                 # 링크가 깨졌을 때 다시 찾을 검색어
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    crawl_status: Mapped[str | None] = mapped_column(String(30))               # crawler/report.py 상태 코드 · RELINKED
    crawl_fail_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('0'))
    last_error: Mapped[str | None] = mapped_column(Text)
    relinked_to: Mapped[str | None] = mapped_column(String(768))               # 링크 복구로 찾은 새 링크
    fingerprint_hash: Mapped[str | None] = mapped_column(CHAR(64))             # 수집 내용 해시. 같으면 다시 보내지 않는다
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=CREATED_AT)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=UPDATED_AT)
