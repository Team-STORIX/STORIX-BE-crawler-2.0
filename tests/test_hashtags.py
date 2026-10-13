"""해시태그 정리 규칙 B (#27)."""
import pytest

from review.catalog import EnumCatalog
from review.hashtags import clean_hashtags
from review.rules import validate_item

# dev BE 장르 목록 (2026-10-10)
GENRES = [('ROMANCE', '로맨스'), ('FANTASY', '판타지'), ('DAILY', '일상'), ('ROFAN', '로판'), ('HISTORICAL', '무협'),
          ('DRAMA', '드라마'), ('GAG', '개그'), ('THRILLER', '스릴러'), ('ACTION', '액션'), ('SPORTS', '스포츠'),
          ('SENTIMENTAL', '감성'), ('BL', 'BL'), ('MODERN_FANTASY', '현판')]
CATALOG = EnumCatalog({
    'platform': {'storedAs': 'name', 'values': [{'name': 'NAVER_WEBTOON', 'dbValue': '네이버 웹툰'}]},
    'genre': {'storedAs': 'dbValue', 'values': [{'name': n, 'dbValue': v} for n, v in GENRES]},
    'ageClassification': {'storedAs': 'dbValue', 'values': [{'name': 'ALL', 'dbValue': '전체연령가'}]},
    'worksType': {'storedAs': 'dbValue', 'values': [{'name': 'WEBTOON', 'dbValue': '웹툰'}]},
})


def clean(tags, genre):
    return clean_hashtags(tags, genre, CATALOG)


def test_sinbi_apartment_example():
    # 이슈 #27 예: 신비아파트(판타지)
    tags = ['애니메이션', '현대판타지', '오컬트판타지', '판타지', '이능력', '우정', '공포', '괴담']
    assert clean(tags, 'FANTASY') == ['애니메이션', '현대판타지', '오컬트판타지', '이능력', '우정', '공포', '괴담']


def test_same_genre_tag_is_removed():
    # 회귀수선전(무협): 네이버가 장르 태그를 뒤에 한 번 더 붙인다
    assert clean(['선협', '무협/사극', '회귀'], 'HISTORICAL') == ['선협', '회귀']
    assert clean(['로맨스', '첫사랑'], 'ROMANCE') == ['첫사랑']
    assert clean(['로맨스판타지', '로판', '빙의'], 'ROFAN') == ['빙의']


@pytest.mark.parametrize('tag,genre,expected', [
    ('현대판타지', 'FANTASY', ['현대판타지']),  # 현판은 따로 있는 장르 → 표기 그대로
    ('로맨스판타지', 'FANTASY', ['로맨스판타지']),
    ('액션', 'FANTASY', ['액션']),               # 다른 장르 태그는 남긴다
    ('오컬트판타지', 'FANTASY', ['오컬트판타지']),  # 세부 장르는 합치지 않는다
    ('학원로맨스', 'ROMANCE', ['학원로맨스']),
    ('성장드라마', 'DRAMA', ['성장드라마']),
    ('판타지물', 'FANTASY', []),
    ('무협물', 'HISTORICAL', []),
])
def test_genre_tags(tag, genre, expected):
    assert clean([tag], genre) == expected


def test_no_work_genre_keeps_genre_tags():
    assert clean(['판타지', '오컬트판타지'], None) == ['판타지', '오컬트판타지']


@pytest.mark.parametrize('tag', [
    '2025 지상최대공모전', '2024 최강자전', '2025 연재직행열차', '최강자전', '독자PICK', '요즘핫한추천작',
    '지금추천작', '명작', '몰아보기', '소설원작', '원작소설有', '성인웹툰', '드라마&영화 원작웹툰',
    '레드아이스 스튜디오', '완결액션', '검수테스트', '', '  ', '#',
])
def test_noise_tags_removed(tag):
    assert clean([tag, '회귀'], 'FANTASY') == ['회귀']


def test_hash_space_case_duplicates_keep_first_in_order():
    assert clean(['#집착남', '능력 녀', '능력녀', 'SF', 'sf', '집착남'], 'ROMANCE') == ['집착남', '능력 녀', 'SF']


def test_validate_item_cleans_hashtags():
    v = validate_item({
        'platform': 'NAVER_WEBTOON', 'works_name': '신비아파트', 'artist_name': '고스트', 'genre': '판타지',
        'age_classification': '전체연령가', 'works_type': '웹툰', 'description': '소개',
        'thumbnail_url': 'https://image-comic.pstatic.net/a.jpg',
        'source_url': 'https://comic.naver.com/webtoon/list?titleId=1',
        'hashtags': ['오컬트판타지', '판타지', '공포', '2025 지상최대공모전'],
    }, CATALOG)
    assert v.normalized['hashtags'] == ['오컬트판타지', '공포']


def test_format_tags_are_noise():
    from review.hashtags import clean_hashtags
    assert clean_hashtags(['e북', 'E북', '만화', '애니화'], None, CATALOG) == ['애니화']
