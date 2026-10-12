"""노션 작품 추가 요청 자동 처리 (#66)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawler.modes.requests import FALLBACK_KEYS, decide, plan  # noqa: E402
from review.notion import NotionRequests, Request, parse_page  # noqa: E402


def page(pid='p1', num=441, title='뉴비 키워서 갈아먹기', types=('웹툰',), platforms=('리디북스',), memo=''):
    return {'id': pid, 'properties': {
        '작품 이름 ': {'type': 'title', 'title': [{'plain_text': title}]},
        'ID': {'type': 'unique_id', 'unique_id': {'number': num}},
        '작품 형태': {'type': 'multi_select', 'multi_select': [{'name': t} for t in types]},
        '연재처': {'type': 'multi_select', 'multi_select': [{'name': p} for p in platforms]},
        '메모': {'type': 'rich_text', 'rich_text': [{'plain_text': memo}] if memo else []},
        '추가 여부': {'type': 'checkbox', 'checkbox': False},
    }}


def test_parse_page():
    r = parse_page(page(memo='19금이에요'))
    assert (r.id, r.title, r.types, r.platforms, r.memo) == (441, '뉴비 키워서 갈아먹기', ['웹툰'], ['리디북스'], '19금이에요')


def test_plan_maps_platforms_and_types():
    reqs = [
        Request('a', 441, '혼불', ['웹소설'], ['리디북스']),
        Request('b', 442, '외모지상주의', ['웹툰'], ['네이버 웹툰', '네이버 시리즈']),
        Request('c', 443, '블랙 스완', ['웹툰', '웹소설'], ['카카오 웹툰']),
        Request('d', 444, '천관사복', ['단행본'], ['네이버 시리즈']),
    ]
    p = plan(reqs)
    assert p['ridibooks'] == [('혼불', '웹소설')]
    assert ('외모지상주의', '웹툰') in p['naver_webtoon'] and ('외모지상주의', '웹툰') in p['naver_series']
    assert p['kakao_page'] == [('블랙 스완', '웹툰'), ('블랙 스완', '웹소설')]  # 카카오 웹툰 → 카카오페이지
    assert ('천관사복', '웹소설') in p['naver_novel']  # 시리즈 웹소설 요청은 네이버 웹소설도 본다


def test_plan_searches_everywhere_for_unsearchable_platforms():
    p = plan([Request('a', 445, '타치바나 백작', ['웹툰'], ['레진코믹스', '기타'])])
    assert set(p) == set(FALLBACK_KEYS)


def test_decide():
    assert not decide([], []).done
    rows = [{'works_name': '혼불', 'works_type': '웹소설', 'status': 'IMPORTED', 'imported_works_id': 16013,
             'import_error': None, 'violations': []}]
    o = decide(['https://ridibooks.com/books/4163000001'], rows)
    assert o.done and o.works_ids == [16013]

    review = [{'works_name': '청사과 낙원 시즌2', 'works_type': '웹툰', 'status': 'NEEDS_REVIEW', 'imported_works_id': None,
               'import_error': None, 'violations': [{'code': 'SUSPECTED_DUPLICATE', 'severity': 'NEEDS_REVIEW'},
                                                    {'code': 'MISSING', 'severity': 'INFO'}]}]
    o = decide(['u'], review)
    assert not o.done and 'SUSPECTED_DUPLICATE' in o.note and 'MISSING' not in o.note


class _Fake(NotionRequests):
    def __init__(self, pages):
        super().__init__('t', 'db')
        self.pages, self.calls = pages, []

    def _call(self, method, path, body=None):
        self.calls.append((method, path, body))
        return {'results': self.pages, 'has_more': False} if method == 'POST' else {}


def test_pending_filters_unprocessed_from_min_id():
    n = _Fake([page('p2', 450, '나중 요청'), page('p1', 441), page('p3', 452, '')])
    reqs = n.pending(441)
    assert [r.id for r in reqs] == [441, 450]  # 제목 없는 행은 건너뛴다
    flt = n.calls[0][2]['filter']['and']
    assert {'property': 'ID', 'unique_id': {'greater_than_or_equal_to': 441}} in flt
    assert {'property': '추가 여부', 'checkbox': {'equals': False}} in flt


def test_mark_review_keeps_requester_memo():
    n = _Fake([])
    n.mark_review(parse_page(page(memo='19금이에요')), '연재처에서 작품을 찾지 못했습니다')
    props = n.calls[0][2]['properties']
    assert props['적재 검토 중'] == {'checkbox': True}
    assert props['메모']['rich_text'][0]['text']['content'] == '19금이에요\n[크롤러] 연재처에서 작품을 찾지 못했습니다'


# ---------------------------------------------------------------- dry-run 이어받기 (#68)

def test_state_round_trip_and_latest_unresumed(tmp_path):
    import json
    import os
    from crawler.modes.requests import latest_state, save_state

    reqs = [Request('a', 441, '혼불', ['웹소설'], ['리디북스'])]
    old = save_state(tmp_path / '2026-10-11', 'prod', 441, reqs, {'혼불': ['u1', 'u1'], '다른 요청': ['x']}, ['r0'])
    new = save_state(tmp_path / '2026-10-12', 'prod', 441, reqs, {'혼불': ['u2']}, ['r1'])
    os.utime(old, (1, 1))
    save_state(tmp_path / '2026-10-12', 'dev', 441, reqs, {}, [])

    state = json.loads(new.read_text(encoding='utf-8'))
    assert state['requests'][0] == {'page_id': 'a', 'id': 441, 'title': '혼불', 'types': ['웹소설'],
                                    'platforms': ['리디북스'], 'memo': ''}
    assert json.loads(old.read_text(encoding='utf-8'))['found'] == {'혼불': ['u1']}  # 요청 밖 제목 · 중복 링크는 뺀다

    assert latest_state('prod', tmp_path) == new
    state['resumed_at'] = '2026-10-12T10:00:00'
    new.write_text(json.dumps(state), encoding='utf-8')
    assert latest_state('prod', tmp_path) == old  # 이미 반영한 건 건너뛴다
    assert latest_state('stage', tmp_path) is None


def test_resume_marks_only_still_pending_requests(tmp_path, monkeypatch):
    import json
    from crawler.modes import requests as mode
    from crawler.modes.requests import resume_requests, save_state
    from review.notion import NotionError

    reqs = [Request('p1', 441, '못 찾은 요청'), Request('p2', 442, '그사이 사람이 처리한 요청')]
    path = save_state(tmp_path, 'prod', 441, reqs, {}, [])
    fake = _Fake([page('p1', 441, '못 찾은 요청')])
    monkeypatch.setattr(mode, 'NotionRequests', lambda: fake)

    summary = resume_requests('prod', str(path))
    assert (summary['requests'], summary['review']) == (1, 1)
    patched = [c for c in fake.calls if c[0] == 'PATCH']
    assert [c[1] for c in patched] == ['/pages/p1']
    assert json.loads(path.read_text(encoding='utf-8'))['resumed_at']

    with pytest.raises(NotionError):  # 같은 dry-run 을 두 번 반영하지 않는다
        resume_requests('prod', str(path))
    with pytest.raises(NotionError):  # 다른 환경 상태 파일은 받지 않는다
        resume_requests('dev', str(save_state(tmp_path / 'x', 'prod', 441, reqs, {}, [])))
