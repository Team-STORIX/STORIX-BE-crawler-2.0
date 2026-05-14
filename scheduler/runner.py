import logging
import os
import signal
import sys

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from scheduler.jobs import (
    job_initial_naver,
    job_initial_kakao,
    job_initial_ridibooks,
    job_new_works_naver,
    job_new_works_kakao,
    job_update_fields_naver,
    job_update_fields_kakao,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

TZ = os.getenv('SCHED_TIMEZONE', 'Asia/Seoul')

SCHEDULE = [
    {
        'id': 'initial_naver',
        'func': job_initial_naver,
        'trigger': CronTrigger(day=1, hour=int(os.getenv('SCHED_INITIAL_NAVER_HOUR', '3')), minute=0, timezone=TZ),
    },
    {
        'id': 'initial_kakao',
        'func': job_initial_kakao,
        'trigger': CronTrigger(day=1, hour=int(os.getenv('SCHED_INITIAL_KAKAO_HOUR', '6')), minute=0, timezone=TZ),
    },
    {
        'id': 'initial_ridibooks',
        'func': job_initial_ridibooks,
        'trigger': CronTrigger(day=1, hour=int(os.getenv('SCHED_INITIAL_RIDIBOOKS_HOUR', '9')), minute=0, timezone=TZ),
    },
    {
        'id': 'new_works_naver',
        'func': job_new_works_naver,
        'trigger': CronTrigger(hour=int(os.getenv('SCHED_NEW_WORKS_HOUR', '9')), minute=0, timezone=TZ),
    },
    {
        'id': 'new_works_kakao',
        'func': job_new_works_kakao,
        'trigger': CronTrigger(hour=int(os.getenv('SCHED_NEW_WORKS_HOUR', '9')), minute=30, timezone=TZ),
    },
    {
        'id': 'update_fields_naver',
        'func': job_update_fields_naver,
        'trigger': CronTrigger(
            day_of_week=os.getenv('SCHED_UPDATE_FIELDS_WEEKDAY', 'mon'),
            hour=int(os.getenv('SCHED_UPDATE_FIELDS_HOUR', '2')),
            minute=0,
            timezone=TZ,
        ),
    },
    {
        'id': 'update_fields_kakao',
        'func': job_update_fields_kakao,
        'trigger': CronTrigger(
            day_of_week=os.getenv('SCHED_UPDATE_FIELDS_WEEKDAY', 'mon'),
            hour=int(os.getenv('SCHED_UPDATE_FIELDS_HOUR', '2')),
            minute=30,
            timezone=TZ,
        ),
    },
]


def main():
    scheduler = BlockingScheduler(timezone=TZ)

    for job in SCHEDULE:
        scheduler.add_job(
            job['func'],
            trigger=job['trigger'],
            id=job['id'],
            max_instances=1,
            coalesce=True,
            misfire_grace_time=600,
        )
        log.info('잡 등록: %s', job['id'])

    def _shutdown(sig, _frame):
        log.info('종료 신호 수신 (%s). 스케줄러 정상 종료...', sig)
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    log.info('스케줄러 시작 (타임존: %s)', TZ)
    for job in scheduler.get_jobs():
        log.info('  %-30s 트리거: %s', job.id, job.trigger)

    scheduler.start()


if __name__ == '__main__':
    main()
