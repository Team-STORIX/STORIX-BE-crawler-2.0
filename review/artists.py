"""작가명 정규화.

크롤러마다 작가명 형식이 다르다.
  네이버 웹툰 · 리디   'a, b'
  카카오              'a ∙ 글 / b ∙ 그림'  (역할 라벨 포함)
  시리즈 · 웹소설      작가 한 칸
  카카오 원작 칸        '작가/출판사' (묵향동후/진강문학성) 또는 공동 작가 'a/b'

BE 에는 한 형식으로 보낸다: 역할 라벨을 떼고, 원작 → 글 → 그림 순으로 이름 단위 중복을 지워 'a, b, c'.
(기존 modules/db_handler 는 필드 단위로만 중복을 지워 'a, b, a' 가 생겼다)
"""
import re

# 역할 라벨로만 쓰이는 토큰. 이름 앞(카카오 '글 홍길동')이나 뒤(네이버 '홍길동 ∙ 글/그림')에 붙는다
_ROLE_TOKEN = re.compile(r'^(?:글|그림|원작|각색|작화|글/그림|글/원작|그림/원작)$')
_ROLE_OF = (('글', '글'), ('각색', '글'), ('그림', '그림'), ('작화', '그림'), ('원작', '원작'))


# '작가/출판사' 로 들어오는 원작 출판사 · 플랫폼 (카카오 원작 칸 등). 작가명에서 뗀다
# 처음 보는 출판사가 섞여 들어오면 여기에 추가한다
PUBLISHERS = (
    '진강문학성', '晋江文学城', 'jjwxc',
    'bilibili comics', 'bilibili', '빌리빌리',
    'china literature', '열문', '閱文',
)
_PUBLISHER_KEYS = {re.sub(r'\s+', '', p).lower() for p in PUBLISHERS}


def _expand_slash(name: str) -> list[str]:
    """'묵향동후/진강문학성' → ['묵향동후'] (출판사 제거), '고문종/이태욱' → ['고문종', '이태욱'] (공동 작가)"""
    if '/' not in name:
        return [name]
    parts = [p.strip() for p in name.split('/') if p.strip()]
    return [p for p in parts if _key(p) not in _PUBLISHER_KEYS]


def _key(name: str) -> str:
    return re.sub(r'\s+', '', name).lower()


def _strip_roles(name: str) -> str:
    tokens = name.split()
    while tokens and _ROLE_TOKEN.match(tokens[0]):
        tokens.pop(0)
    while tokens and _ROLE_TOKEN.match(tokens[-1]):
        tokens.pop()
    return ' '.join(tokens)


def _split_names(value) -> list[str]:
    """역할 칸 값 → 이름들. 칸 안에 붙어 온 역할 라벨('작화 스푼')도 뗀다"""
    if not isinstance(value, str):
        return []
    names = [n for n in (_strip_roles(x.strip()) for x in value.split(',')) if n]
    return [part for n in names for part in _expand_slash(n)]


def _dedupe(names: list[str]) -> list[str]:
    seen, out = set(), []
    for n in names:
        k = _key(n)
        if k and k not in seen:
            seen.add(k)
            out.append(n)
    return out


def parse_raw(raw) -> list[tuple[str, set[str]]]:
    """원본 작가명 → [(이름, 역할들)]. 역할을 모르면 빈 집합."""
    if not isinstance(raw, str) or not raw.strip():
        return []
    text = raw.replace('∙', ' ').replace(':', ' ')  # 가운뎃점(·)은 이름에 쓰여 그대로 둔다
    out = []
    for part in re.split(r'\s/\s|,', text):
        tokens = part.split()
        roles: set[str] = set()
        while tokens and _ROLE_TOKEN.match(tokens[0]):
            tok = tokens.pop(0)
            roles |= {r for w, r in _ROLE_OF if w in tok}
        while tokens and _ROLE_TOKEN.match(tokens[-1]):
            tok = tokens.pop()
            roles |= {r for w, r in _ROLE_OF if w in tok}
        name = ' '.join(tokens).strip()
        for part in _expand_slash(name) if name else []:
            out.append((part, roles))
    return out


def normalize_artists(item: dict) -> dict:
    """artist_name · author · illustrator · original_author 를 정규화해 돌려준다."""
    parsed = parse_raw(item.get('artist_name'))

    def field(key: str, role: str) -> list[str]:
        explicit = _split_names(item.get(key))
        if explicit:
            return _dedupe(explicit)
        return _dedupe([n for n, roles in parsed if role in roles])

    original = field('original_author', '원작')
    author = field('author', '글')
    illustrator = field('illustrator', '그림')

    # 역할 표기가 없는 원본 이름(시리즈 · 웹소설 단일 작가 등)도 빠뜨리지 않는다
    unlabeled = [n for n, roles in parsed if not roles]
    artists = _dedupe(original + author + illustrator + unlabeled)
    if not author and unlabeled:
        author = _dedupe(unlabeled[:1])

    return {
        'artist_name': ', '.join(artists),
        'author': ', '.join(author),
        'illustrator': ', '.join(illustrator),
        'original_author': ', '.join(original),
    }
