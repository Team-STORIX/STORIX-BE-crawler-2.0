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
    if not path.exists():
        log.warning('[import] 적재할 파일 없음: %s', path)
        return

    from batch.importer import run_import
    run_import(str(path))
