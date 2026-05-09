import logging
from datetime import date
from pathlib import Path

from config import OUTPUT_DIR

log = logging.getLogger(__name__)


def _today_output_dir() -> Path:
    return OUTPUT_DIR / date.today().strftime('%Y-%m-%d')


def job_initial_naver():
    log.info('[job] 네이버 웹툰 initial 크롤링 시작')
    try:
        from crawler.output.jsonl_writer import JSONLWriter
        from crawler.modes.initial import run_naver_webtoon
        with JSONLWriter(platform='naver_webtoon', mode='initial') as writer:
            run_naver_webtoon(writer)
        log.info('[job] 네이버 웹툰 initial 완료 → %d건', writer.count)
        _import_today('naver_webtoon')
    except Exception as e:
        log.error('[job] 네이버 웹툰 initial 실패: %s', e, exc_info=True)


def job_initial_kakao():
    log.info('[job] 카카오페이지 initial 크롤링 시작')
    try:
        from crawler.output.jsonl_writer import JSONLWriter
        from crawler.modes.initial import run_kakao_page
        with JSONLWriter(platform='kakao_page', mode='initial') as writer:
            run_kakao_page(writer)
        log.info('[job] 카카오페이지 initial 완료 → %d건', writer.count)
        _import_today('kakao_page')
    except Exception as e:
        log.error('[job] 카카오페이지 initial 실패: %s', e, exc_info=True)


def job_new_works_naver():
    log.info('[job] 네이버 신작 크롤링 시작')
    try:
        from crawler.output.jsonl_writer import JSONLWriter
        from crawler.modes.new_works import run_naver_webtoon
        with JSONLWriter(platform='naver_webtoon', mode='new_works') as writer:
            run_naver_webtoon(writer)
        log.info('[job] 네이버 신작 완료 → %d건', writer.count)
        _import_today('naver_webtoon')
    except Exception as e:
        log.error('[job] 네이버 신작 실패: %s', e, exc_info=True)


def job_new_works_kakao():
    log.info('[job] 카카오 신작 크롤링 시작')
    try:
        from crawler.output.jsonl_writer import JSONLWriter
        from crawler.modes.new_works import run_kakao_page
        with JSONLWriter(platform='kakao_page', mode='new_works') as writer:
            run_kakao_page(writer)
        log.info('[job] 카카오 신작 완료 → %d건', writer.count)
        _import_today('kakao_page')
    except Exception as e:
        log.error('[job] 카카오 신작 실패: %s', e, exc_info=True)


def job_update_fields_naver():
    log.info('[job] 네이버 update_fields 시작')
    try:
        from crawler.modes.update_fields import run_update_fields
        run_update_fields('naver_webtoon', str(OUTPUT_DIR))
    except Exception as e:
        log.error('[job] 네이버 update_fields 실패: %s', e, exc_info=True)


def job_update_fields_kakao():
    log.info('[job] 카카오 update_fields 시작')
    try:
        from crawler.modes.update_fields import run_update_fields
        run_update_fields('kakao_page', str(OUTPUT_DIR))
    except Exception as e:
        log.error('[job] 카카오 update_fields 실패: %s', e, exc_info=True)


def _import_today(platform: str):
    today_dir = _today_output_dir()
    if not today_dir.exists():
        log.warning('[import] 오늘 output 디렉토리 없음: %s', today_dir)
        return
    files = sorted(today_dir.glob(f'{platform}_*.jsonl'))
    if not files:
        log.warning('[import] 적재할 파일 없음: %s/%s_*.jsonl', today_dir, platform)
        return

    from batch.importer import run_import
    run_import(str(files[-1]))
