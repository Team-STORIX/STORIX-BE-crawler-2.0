"""작품 제목 규칙 한 곳 (#122). 크롤러 · 검색 · 링크 채우기 · 복구 · 검수(review/rules)가 같이 쓴다.

두 가지를 구분한다
  - 작품명으로 저장할 때(clean_work_name · strip_volume_suffix): 연재 · 판매 상태 라벨만 뗀다.
    판본([19세 완전판])과 묶음 이름('[과다분출 단편선] 네 꿈을 꿀게')은 작품명이라 남긴다
  - 같은 작품인지 비교할 때(strip_title_labels · title_match): 판본 표기만 남기고 [] 는 앞뒤 무엇이든 뗀다
"""
import re
from difflib import SequenceMatcher

# 판본. BE 가 다른 작품으로 판정하므로 본편과 함께 있으면 둘 다 수집한다 (2026-10-10 사용자 결정)
EDITION_WORDS = ('완전판', '개정판')
# 본편에 딸린 이야기. 같은 작품이라 따로 수집하지 않는다 — 검색어에 없으면 후보에서 뺀다.
# 외전이 19세여도 작품 연령은 본편 기준이다 (2026-10-10 사용자 결정)
SIDE_STORY_WORDS = ('외전', '특별편', '번외')
# 앞부분 일치에서 뒤에 이어져도 되는 부제 · 판본 표기의 시작 글자
SUBTITLE_SEPARATORS = '-–—:~(（[<〈《'
# 같은 작품으로 볼 유사도 (정확 · 앞부분 일치로 못 잡은 후보의 마지막 순위)
FUZZY_THRESHOLD = 0.9

_EDITION = '|'.join(EDITION_WORDS)
_EDITION_LABEL = re.compile(r'\s*[\[(][^\])]*(?:%s)[^\])]*[\])]' % _EDITION)
# 연재 · 판매 상태 라벨. 작품명이 아니다 ('넷카마 펀치!!! [완결]', '[e북] 마도조사', '화산귀환 [독점]').
# 리디 e북은 개정판만 팔 때 제목 앞에 '개정판 | ' 을 붙인다 ('개정판 | 블랙 스완'). 판본 표기가 아니라 판매 표기다
# (2026-10-10 사용자 결정: 개정판 표기는 본편이 따로 있을 때만 남긴다)
_STATUS_LABEL = re.compile(r'\s*\[(?:완결|독점|선독점|휴재|연재|단행본|e북)\]\s*|^\s*개정판\s*\|\s*')
# 비교할 때 떼는 라벨: 판본 표기가 아닌 [] 는 앞뒤 무엇이든 (+ '개정판 | ')
_COMPARE_LABEL = re.compile(r'\s*\[(?![^\]]*(?:%s))[^\]]*\]\s*|^\s*개정판\s*\|\s*' % _EDITION)
_SPACES = re.compile(r'\s{2,}')
# 작품명 끝의 권수 표기. 권 단위 묶음 상품 이름이라 작품명에서 뗀다 (2026-10-10 사용자 결정)
#   '테이밍(The Taming) 2권' · '… 3~5권' · '… (1~3권)'
# '권' 이 없는 숫자 범위는 떼지 않는다. 네이버 웹툰은 '사이드킥 2~3' · '하이브 1~2' 처럼 시즌을 범위로 쓰고,
# 떼면 시즌 1('사이드킥')과 같은 작품으로 붙는다. 시즌 · 부도 BE 가 다른 작품으로 보므로 그대로 둔다
_VOLUME_SUFFIX = re.compile(r'\s*(?:\d+\s*[~\-]\s*\d+\s*권|\d+\s*권|[(\[]\s*\d+(?:\s*[~\-]\s*\d+)?\s*권\s*[)\]])\s*$')

# 작품명 자리에 들어오면 잘못 읽은 것: 상세 페이지의 섹션 제목 · 사이트 이름
# (2026-10 카카오 화면 개편 때 작품명이 전부 "줄거리"로 수집됨)
NOT_A_TITLE = {'줄거리', '작품소개', '작품 소개', '소개', '키워드', '상세정보', '작품정보', '작품 정보', '동일작',
               '이 작가의 다른 작품',
               '카카오페이지', '콘텐츠홈', '네이버 웹툰', '네이버 웹소설', '네이버 시리즈', '리디', '리디북스'}


def is_edition(text: str) -> bool:
    return any(w in (text or '') for w in EDITION_WORDS)


def strip_edition_labels(title: str) -> str:
    """'응급 플러팅 [19세 완전판]' → '응급 플러팅'. 판본끼리 · 본편과 같은 제목인지 비교할 때"""
    return _EDITION_LABEL.sub('', title or '').strip()


def clean_work_name(title: str) -> str:
    """플랫폼 제목 → 저장할 작품명. 연재 · 판매 상태 라벨만 뗀다 ('화산귀환 [독점]' → '화산귀환')."""
    return _SPACES.sub(' ', _STATUS_LABEL.sub(' ', title or '')).strip()


def strip_volume_suffix(name: str) -> str:
    """'마도조사 [19세 완전판][단행본] 3권' → '마도조사 [19세 완전판]'. 다 떼서 빈 값이면 원래 이름."""
    stripped = _VOLUME_SUFFIX.sub('', clean_work_name(name)).strip()
    return stripped or (name or '')


def strip_title_labels(title: str) -> str:
    """비교용: 판본 표기만 남기고 [] 를 뗀다 ('[과다분출 단편선] 네 꿈을 꿀게' → '네 꿈을 꿀게')."""
    return _SPACES.sub(' ', _COMPARE_LABEL.sub(' ', title or '')).strip()


def norm_title(s: str) -> str:
    """제목 비교용 정규화: 공백·괄호·구분기호(·∙#) 제거 후 소문자화."""
    return re.sub(r'[\s\[\]()·∙#]', '', s or '').lower()


def title_ratio(norm_a: str, norm_b: str) -> float:
    """정규화된 두 제목의 유사도(0.0~1.0). SequenceMatcher 기반."""
    if not norm_a or not norm_b:
        return 0.0
    return SequenceMatcher(None, norm_a, norm_b).ratio()


def _rest_after_prefix(short: str, long_: str) -> str:
    """long_ 에서 short 와 정규화 기준으로 겹치는 앞부분을 뺀 나머지 (원문 그대로)."""
    want = norm_title(short)
    i = 0
    for pos, ch in enumerate(long_):
        n = norm_title(ch)
        if not n:
            continue
        if i >= len(want):
            return long_[pos:]
        if want[i:i + len(n)] != n:
            return long_[pos:]
        i += len(n)
        if i >= len(want):
            return long_[pos + 1:]
    return ''


def title_match(query: str, text: str, threshold: float = FUZZY_THRESHOLD) -> tuple[int, float] | None:
    """검색어와 결과 제목의 일치 정도. (0 정확 | 1 앞부분 | 2 유사, 유사도), 불일치면 None.

    부분 일치는 한쪽이 다른 쪽의 앞부분일 때만 인정한다. 부제 · 라벨이 뒤에 붙는 경우
    ('레지나레나' → '레지나레나 - 용서받지 못한 그대에게')는 잡고, 앞에 다른 말이 붙은
    다른 작품('헌터는 조용히 살고 싶다' → '은퇴한 C급 헌터는 조용히 살고 싶다')은 거른다.
    """
    q_raw, t_raw = strip_title_labels(query), strip_title_labels(text)
    q, t = norm_title(q_raw), norm_title(t_raw)
    if not q or not t:
        return None
    if q == t:
        return 0, 1.0
    if t.startswith(q) or q.startswith(t):
        # 뒤에 붙은 부분이 부제 · 판본 표기일 때만 같은 작품 계열로 본다.
        # '상수리나무 아래 4컷 만화' 처럼 다른 말이 이어지면 다른 작품이다
        short, long_ = (q_raw, t_raw) if len(q) <= len(t) else (t_raw, q_raw)
        # 짧은 쪽이 괄호로 끝나면('Deep(딥)') 나머지가 닫는 괄호부터 시작한다 → ' [개정판]'
        rest = _rest_after_prefix(short, long_).lstrip(' )]）')
        if rest and rest[0] not in SUBTITLE_SEPARATORS:
            return None
        return 1, min(len(q), len(t)) / max(len(q), len(t))
    ratio = title_ratio(q, t)
    if ratio >= threshold:
        return 2, ratio
    return None


def same_title(a: str, b: str) -> bool:
    """판본까지 같은 같은 제목인지 (링크 채우기의 정확 일치 키)."""
    return norm_title(strip_title_labels(a)) == norm_title(strip_title_labels(b))
