import logging
from pathlib import Path

from config import OUTPUT_DIR

log = logging.getLogger(__name__)


def job_initial_naver():
    log.info('[job] 네이버 웹툰 initial 크롤링 시작')
    try:
        from crawler.output.jsonl_writer import JSONLWriter
        from crawler.modes.initial import run_naver_webtoon
        with JSONLWriter(
            platform='naver_webtoon',
            mode='initial',
            filename='naver_webtoon_initial.jsonl',
        ) as writer:
            run_naver_webtoon(writer)
        log.info('[job] 네이버 웹툰 initial 완료 → %d건', writer.count)
        _import_file(writer.path)
    except Exception as e:
        log.error('[job] 네이버 웹툰 initial 실패: %s', e, exc_info=True)


def job_initial_kakao():
    log.info('[job] 카카오페이지 initial 크롤링 시작')
    try:
        from crawler.output.jsonl_writer import JSONLWriter
        from crawler.modes.initial import run_kakao_page
        with JSONLWriter(
            platform='kakao_page',
            mode='initial',
            filename='kakao_page_initial.jsonl',
        ) as writer:
            run_kakao_page(writer)
        log.info('[job] 카카오페이지 initial 완료 → %d건', writer.count)
        _import_file(writer.path)
    except Exception as e:
        log.error('[job] 카카오페이지 initial 실패: %s', e, exc_info=True)


def job_initial_ridibooks():
    log.info('[job] 리디북스 initial 크롤링 시작')
    try:
        from crawler.output.jsonl_writer import JSONLWriter
        from crawler.modes.initial import run_ridibooks
        with JSONLWriter(
            platform='ridibooks',
            mode='initial',
            filename='ridibooks_initial.jsonl',
        ) as writer:
            run_ridibooks(writer)
        log.info('[job] 리디북스 initial 완료 → %d건', writer.count)
        _import_file(writer.path)
    except Exception as e:
        log.error('[job] 리디북스 initial 실패: %s', e, exc_info=True)


def job_new_works_naver():
    log.info('[job] 네이버 신작 크롤링 시작')
    try:
        from crawler.output.jsonl_writer import JSONLWriter
        from crawler.modes.new_works import run_naver_webtoon
        with JSONLWriter(
            platform='naver_webtoon',
            mode='new_works',
            filename='naver_webtoon_new_works.jsonl',
        ) as writer:
            run_naver_webtoon(writer)
        log.info('[job] 네이버 신작 완료 → %d건', writer.count)
        _import_file(writer.path)
    except Exception as e:
        log.error('[job] 네이버 신작 실패: %s', e, exc_info=True)


def job_new_works_kakao():
    log.info('[job] 카카오 신작 크롤링 시작')
    try:
        from crawler.output.jsonl_writer import JSONLWriter
        from crawler.modes.new_works import run_kakao_page
        with JSONLWriter(
            platform='kakao_page',
            mode='new_works',
            filename='kakao_page_new_works.jsonl',
        ) as writer:
            run_kakao_page(writer)
        log.info('[job] 카카오 신작 완료 → %d건', writer.count)
        _import_file(writer.path)
    except Exception as e:
        log.error('[job] 카카오 신작 실패: %s', e, exc_info=True)


def job_update_fields_naver():
    log.info('[job] 네이버 update_fields 시작')
    try:
        from crawler.modes.update_fields import run_update_fields
        for path in run_update_fields('naver_webtoon', str(OUTPUT_DIR), platform_filenames=True):
            _import_file(path)
    except Exception as e:
        log.error('[job] 네이버 update_fields 실패: %s', e, exc_info=True)


def job_update_fields_kakao():
    log.info('[job] 카카오 update_fields 시작')
    try:
        from crawler.modes.update_fields import run_update_fields
        for path in run_update_fields('kakao_page', str(OUTPUT_DIR), platform_filenames=True):
            _import_file(path)
    except Exception as e:
        log.error('[job] 카카오 update_fields 실패: %s', e, exc_info=True)


def _import_file(path: Path):
    """수집 결과를 검수 staging 에 적재한다(Layer 1 · 1.5 판정). BE 로는 보내지 않는다.

    서비스 DB 에 직접 넣던 batch import 는 없앴다(#5). BE 반영은 사람이 결과를 보고
    `python cli.py stage import --run-id <런>` 으로 한다. 대상 환경은 STORIX_ENV.
    """
    if not path.exists():
        log.warning('[stage] 적재할 파일 없음: %s', path)
        return

    from review.app import load_catalog
    from review.service import load_run, read_jsonl
    from review.store import StagingStore, connect, ensure_schema

    ensure_schema()
    conn = connect()
    try:
        result = load_run(StagingStore(conn), load_catalog(), read_jsonl(path), f'scheduler_{path.stem}', str(path))
        log.info('[stage] %s → %s', path.name, result)
    finally:
        conn.close()
