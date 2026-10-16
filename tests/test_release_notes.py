from tools.release_notes import included_prs, notes, parse_commit


def test_parse_commit_only_release():
    assert parse_commit('Release v2.2.0 (#111)') == ('v2.2.0', 111)
    assert parse_commit('Release v2.1.5 (#104)\n\n* Fix: …') == ('v2.1.5', 104)
    assert parse_commit('Fix: 크롤러 Chrome 이 USB 장치를 열지 않게 (#103)') is None
    assert parse_commit('Merge pull request #110 from …') is None
    assert parse_commit('') is None


def test_included_prs_cherry_pick_lines():
    body = ('> - [x] PR #106의 커밋 cherry-pick (플랫폼 판별 로직 공통 모듈화)\n'
            '> - [x] PR #108의 커밋 cherry-pick (레진코믹스, 미스터블루 검색 크롤러 추가)\n'
            '> - [x] release/v2.2.0 구성')
    assert included_prs(body) == [('106', '플랫폼 판별 로직 공통 모듈화'), ('108', '레진코믹스, 미스터블루 검색 크롤러 추가')]


def test_included_prs_first_release_table():
    body = '| PR | 내용 |\n|---|---|\n| #2 | Feat: 작품 검수 파이프라인 |\n| #12 | Fix: 정식 계약 전 작품 수집 제외 |\n'
    assert included_prs(body) == [('2', '작품 검수 파이프라인'), ('12', '정식 계약 전 작품 수집 제외')]


def test_notes():
    assert notes('v2.2.0', 111, [('106', 'A'), ('110', 'B')]) == (
        '## STORIX Crawler v2.2.0 릴리즈\n\n- A (#106)\n- B (#110)\n\n포함 PR: #106, #110\nRelease PR: #111\n')
    assert '찾지 못함' in notes('v9.9.9', 1, [])
