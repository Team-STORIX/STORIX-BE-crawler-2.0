"""플랫폼이 글자로 보여주는 연령 · 분류를 정해진 값으로 바꾼다 (#121).

플랫폼마다 따로 해석하면 같은 표기가 다르게 들어간다 ('청소년 이용불가' 가 시리즈는 18세, 카카오는 빈 값이었다).
글자를 읽는 크롤러는 모두 여기를 쓴다. 코드 · 플래그로 내려오는 값(레진 등급 코드, 봄툰 isAdult, 리디 is_adult_only)은
플랫폼 파서가 바꾸되 결과는 같은 이름(AGE_*)을 쓴다.
"""
import re

AGE_18, AGE_15, AGE_12, AGE_ALL = '18세 이용가', '15세 이용가', '12세 이용가', '전체연령가'

_ADULT = ('19', '18세', '청불', '청소년 이용불가', '청소년이용불가', '성인')


def age_from_text(text: str | None) -> str:
    """'19세 이용가' · '청소년 이용불가' · '15세이용가' · '전체 이용가' → 정해진 연령. 모르면 ''."""
    t = (text or '').strip()
    if not t:
        return ''
    if any(k in t for k in _ADULT):
        return AGE_18
    if '15' in t:
        return AGE_15
    if '12' in t:
        return AGE_12
    if '전체' in t or t.lower() == 'all':
        return AGE_ALL
    return ''


def works_type_from_category(text: str | None, default: str = '웹툰') -> str:
    """플랫폼 분류 글자 → 웹툰 · 만화 · 웹소설. 웹툰 > 만화 > 소설(노벨 · e북) 순으로 본다.
    '웹툰 소설원작' 은 웹툰, '만화 e북' 은 출판 만화(#64), '판타지 웹소설' 은 웹소설."""
    t = text or ''
    if '웹툰' in t:
        return '웹툰'
    if '만화' in t:
        return '만화'
    if any(k in t for k in ('소설', '노벨', 'e북')):
        return '웹소설'
    return default


# 작가가 아닌 역할. 작가 칸에 넣지 않는다
NOT_ARTIST_ROLES = ('번역', '옮긴이', '감수', '편집', '기획')


def artist_fields(role: str | None) -> tuple[str, ...]:
    """작가 역할 글자 → 채울 칸. '글/그림' → (author, illustrator), '그린이' → (illustrator,), '번역' → ().
    역할을 모르면(저자 · 지은이 · 역할 없음) 글 작가로 본다."""
    r = (role or '').replace('/', '').replace('·', '').replace(' ', '')
    if any(k in r for k in NOT_ARTIST_ROLES):
        return ()
    if '글' in r and '그림' in r:
        return ('author', 'illustrator')
    if '그림' in r or '그린이' in r:
        return ('illustrator',)
    if '원작' in r:
        return ('original_author',)
    return ('author',)


# 19금 가림 이미지. 성인 인증이 안 된 화면에서 표지 대신 나온다. 표지로 쓰면 안 되므로 비우고 사람이 본다
#  - 리디 static.ridicdn.net/…/book_cover/cover_adult.png (크롤러는 책 ID 로 실제 표지를 다시 받는다)
#  - 카카오페이지 로그인 · 성인 인증이 풀린 화면의 og:image (사이트 공용 공유 이미지)
AGE_GATE_COVER = re.compile(r'book_cover/cover_adult|cover_adult\.|adult_cover|thumb_adult|/adult/(?:cover|thumb)'
                            r'|page\.kakaocdn\.net/pageweb/shared/ogImage')


def is_age_gate_cover(url: str | None) -> bool:
    """표지 대신 나온 19금 가림 이미지인지. 검수(표지 비움)와 리디 크롤러(실제 표지로 바꿈)가 같이 쓴다 (#122)."""
    return bool(AGE_GATE_COVER.search(url or ''))
