"""Alembic 실행 환경. staging DB 주소는 review/store.py 와 같은 값(config.MYSQL_CONFIG + STAGING_DATABASE)을 쓴다."""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from review.models import Base

config = context.config
if config.config_file_name is not None and config.attributes.get('configure_logger', True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    from review.store import staging_url
    return staging_url()


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get('connection')
    if connection is not None:  # ensure_schema() 가 연결을 넘겨준 경우
        _run(connection)
        return
    engine = create_engine(_url())
    with engine.connect() as conn:
        _run(conn)


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
