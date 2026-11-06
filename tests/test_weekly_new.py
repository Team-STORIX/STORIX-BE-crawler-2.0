"""신작 주간 수집 (#158)."""
from datetime import date

from crawler.modes import new_works, weekly_new


def test_platform_for_weekday():
    assert weekly_new.platform_for(date(2026, 11, 2)) == 'naver_webtoon'   # 월
    assert weekly_new.platform_for(date(2026, 11, 3)) == 'kakao_page'      # 화
    assert weekly_new.platform_for(date(2026, 11, 4)) == 'ridibooks'       # 수
    assert weekly_new.platform_for(date(2026, 11, 8)) is None              # 일 — 비움


class FakeStore:
    def __init__(self, collected):
        self.collected, self.asked = collected, None

    def collected_keys(self, keys):
        self.asked = keys
        return {k for k in keys if k in self.collected}


def test_known_filter_matches_by_platform_work_id():
    """링크 모양이 달라도(끝 / · 쿼리) 같은 작품이면 이미 긁은 것으로 본다"""
    store = FakeStore({('KAKAO_PAGE', '111'), ('RIDIBOOKS', '777')})
    known = weekly_new.known_filter(store)
    urls = ['https://page.kakao.com/content/111/', 'https://page.kakao.com/content/222/',
            'https://ridibooks.com/books/777?_rdt_sid=new', 'https://example.com/x']
    assert known(urls) == {urls[0], urls[2]}
    assert ('KAKAO_PAGE', '222') in store.asked and len(store.asked) == 3   # 플랫폼 링크가 아니면 묻지 않는다


def test_only_new_skips_known_and_counts(monkeypatch):
    monkeypatch.setattr(new_works, '_known', lambda urls: {urls[0]})
    new_works.STATS.clear()
    assert new_works._only_new('kakao_page', ['a', 'b', 'c']) == ['b', 'c']
    assert new_works._only_new('kakao_page', ['d']) == []
    assert new_works.STATS['kakao_page'] == {'listed': 4, 'skipped': 2, 'crawled': 2}


def test_only_new_without_filter_keeps_all(monkeypatch):
    monkeypatch.setattr(new_works, '_known', None)
    new_works.STATS.clear()
    assert new_works._only_new('naver_webtoon', ['a', 'b']) == ['a', 'b']
    assert new_works.STATS['naver_webtoon']['skipped'] == 0


def test_run_weekly_new_stops_without_db_on_off_day_or_unsupported(monkeypatch):
    monkeypatch.setattr(weekly_new, 'platform_for', lambda today=None: None)
    assert weekly_new.run_weekly_new() == {'platform': None}
    # 목록 수집기가 아직 없는 플랫폼
    assert weekly_new.run_weekly_new('bomtoon') == {'platform': 'bomtoon', 'supported': False}


def test_only_count_hold_is_released():
    """건수 급변만 풀고, 파서 깨짐 의심(작가 빈 값 · 연령 쏠림 · 장르 하나)은 보류를 유지한다"""
    from review.rules import check_run
    count_only = check_run([{'artist_name': 'a', 'age_classification': '전체연령가', 'genre': g}
                            for g in ('로맨스', '판타지')], prev_count=100)
    assert count_only and weekly_new.suspicious_holds(count_only) == []
    broken = check_run([{'artist_name': '', 'age_classification': a, 'genre': g}
                        for a, g in [('전체연령가', '로맨스'), ('15세 이용가', '판타지')] * 10])
    assert weekly_new.suspicious_holds(broken) == broken and broken


def test_remember_keeps_only_expired_or_blocked_platforms(monkeypatch, tmp_path):
    """세션 만료 · 차단으로 못 긁은 플랫폼만 남겨 [다시 긁기] 가 다시 돈다. 긁으면 지운다"""
    from crawler import report
    monkeypatch.setattr(weekly_new, 'PENDING_FILE', tmp_path / 'pending.json')
    assert weekly_new.pending() == []

    report.reset()
    report.record('kakao_page', report.AUTH_EXPIRED, '플랫폼 중단')
    weekly_new.remember('kakao_page')
    report.reset()
    report.record('ridibooks', report.PARSING_FAILED, 'run-1', '작가 빈 값')   # 다시 긁어도 같다
    weekly_new.remember('ridibooks')
    assert weekly_new.pending() == ['kakao_page']

    report.reset()   # 다시 로그인 뒤 긁음
    weekly_new.remember('kakao_page')
    assert weekly_new.pending() == []
