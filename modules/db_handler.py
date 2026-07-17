import mysql.connector
from mysql.connector import Error as MySQLError
import csv
import os
import re
from config import FAILED_CSV, OUTPUT_CSV

# 토큰 전체가 역할 키워드로만 구성됐는지 판별 (예: '글', '그림', '글그림', '원작', '작화').
# '글쓰는기계'처럼 키워드가 이름의 일부인 경우를 역할 라벨로 오인하지 않기 위함.
# '작화'는 '그림'의 동의어(카카오/리디 일부 표기).
_ROLE_TOKEN_RE = re.compile(r'^(?:글|그림|작화|원작|각색)+$')

# 장르 우선순위
GENRE_PRIORITY = {
    '로판': 5,
    '무협': 1,
    '판타지': 1,
    '액션': 1,
    '드라마': 1,
    '로맨스': 1,
    '일상': 1,
    '개그': 1,
    '스릴러': 1,
}

PRIORITY_CASE_SQL = """
    CASE genre
        WHEN '로판' THEN 5
        WHEN '무협' THEN 1
        WHEN '판타지' THEN 1
        WHEN '액션' THEN 1
        WHEN '드라마' THEN 1
        WHEN '로맨스' THEN 1
        WHEN '일상' THEN 1
        WHEN '개그' THEN 1
        WHEN '스릴러' THEN 1
        ELSE 0
    END
"""


def _clean_text(value, default=""):
    if value is None:
        return default
    return str(value).strip()


def _optional_text(value):
    text = _clean_text(value)
    return text or None


def _clean_hashtags(values):
    if isinstance(values, str):
        values = [values]

    cleaned = []
    for value in values or []:
        tag = _clean_text(value).lstrip("#")
        if tag and tag not in cleaned:
            cleaned.append(tag)
    return cleaned


def _canonical_artist_name(raw_artist_name, author, illustrator, original_author):
    names = []
    seen = set()
    for value in (original_author, author, illustrator):
        name = _clean_text(value)
        if name and name not in seen:
            names.append(name)
            seen.add(name)
    return ", ".join(names) if names else _clean_text(raw_artist_name)


def connect_database(config):
    try:
        conn = mysql.connector.connect(**config)
        if conn.is_connected():
            print("✅ 데이터베이스 연결 성공")
            return conn
    except MySQLError as e:
        print(f"❌ 데이터베이스 연결 실패: {e}")
    return None


def parse_artists(artist_name_raw):

    author, illustrator, original_author = None, None, None
    
    if not artist_name_raw:
        return None, None, None

    # 정규화
    text = artist_name_raw.replace('∙', ' ').replace(':', ' ')
    text = text.replace('글/그림', '글 그림').replace('글/원작', '글 원작')

    parts = [p.strip() for p in text.split('/') if p.strip()]

    for part in parts:
        part = part.strip()
        if not part: continue

        # 공백 토큰 단위로 분리: 토큰 '전체'가 역할 키워드일 때만 역할 라벨로 인정한다.
        # ('글쓰는기계' → 이름) 라벨은 이름 앞(카카오식 '글 홍길동')에도,
        # 이름 뒤(네이버식 '홍길동 ∙ 글/그림')에도 올 수 있어 양쪽을 모두 떼어낸다.
        roles: set[str] = set()
        tokens = part.split()

        def _absorb_role(tok: str) -> None:
            if '글' in tok or '각색' in tok:
                roles.add('글')
            if '그림' in tok or '작화' in tok:
                roles.add('그림')
            if '원작' in tok:
                roles.add('원작')

        while tokens and _ROLE_TOKEN_RE.match(tokens[0]):
            _absorb_role(tokens.pop(0))
        while tokens and _ROLE_TOKEN_RE.match(tokens[-1]):
            _absorb_role(tokens.pop())

        name = ' '.join(tokens).strip()
        if not name:
            continue

        if not roles:
            if not author:
                author = name
            elif not illustrator:
                illustrator = name
        else:
            if '글' in roles: author = name
            if '그림' in roles: illustrator = name
            if '원작' in roles: original_author = name

    if len(parts) == 1 and author and not illustrator and not original_author:
        illustrator = author

    return author, illustrator, original_author


def normalize_data(data):
    # 장르
    genre = _clean_text(data.get('genre')).lstrip('#')
    genre = genre.replace('무협 / 사극', '무협').replace('무협/사극', '무협')
    
    # 연령
    age_raw = _clean_text(data.get('age_classification')).replace(' ', '')
    if not age_raw: 
        age = ""  
    elif any(x in age_raw for x in ['18', '19', '청불']): 
        age = '18세 이용가'
    elif '15' in age_raw: 
        age = '15세 이용가'
    elif '12' in age_raw: 
        age = '12세 이용가'
    elif '전체' in age_raw: 
        age = '전체연령가'
    else: 
        age = ""

    # 작가
    parsed_author, parsed_illustrator, parsed_original_author = parse_artists(
        _clean_text(data.get('artist_name'))
    )
    author = _optional_text(data.get('author')) or parsed_author
    illustrator = _optional_text(data.get('illustrator')) or parsed_illustrator
    original_author = _optional_text(data.get('original_author')) or parsed_original_author
    artist_name = _canonical_artist_name(
        data.get('artist_name'),
        author,
        illustrator,
        original_author,
    )

    return {
        **data,
        'platform': _clean_text(data.get('platform')),
        'works_name': _clean_text(data.get('works_name')),
        'artist_name': artist_name,
        'genre': genre,
        'age_classification': age,
        'description': _clean_text(data.get('description')),
        'thumbnail_url': _clean_text(data.get('thumbnail_url')),
        'works_type': _clean_text(data.get('works_type')),
        'author': author,
        'illustrator': illustrator,
        'original_author': original_author,
        'hashtags': _clean_hashtags(data.get('hashtags')),
        'source_url': _clean_text(data.get('source_url')),
        'priority': GENRE_PRIORITY.get(genre, 0)
    }


def save_one_row(connection, cursor, raw_data):
    data = normalize_data(raw_data)
    hashtag_list = data.get('hashtags', [])
    p = data['priority']
    lock_acquired = False

    try:
        cursor.execute(
            "SELECT GET_LOCK(SHA2(CONCAT('works:', %s, '|', %s), 256), 10)",
            (data['works_name'], data['artist_name']),
        )
        lock_result = cursor.fetchone()
        if not lock_result or lock_result[0] != 1:
            raise TimeoutError(f"작품 저장 락 획득 실패: {data['works_name']}")
        lock_acquired = True

        # 기존 작품 조회 (works_name + artist_name 기준)
        cursor.execute(
            """
            SELECT works_id
            FROM works
            WHERE works_name = %s AND artist_name = %s
            ORDER BY works_id
            LIMIT 1
            """,
            (data['works_name'], data['artist_name'])
        )
        existing = cursor.fetchone()
        changed = False

        if existing:
            works_id = existing[0]
            # 새 값 없음 → 기존 유지 / 기존 없음 → 새 값 / 둘 다 있음 → priority 높은 쪽
            cursor.execute(f"""
                UPDATE works SET
                    age_classification = COALESCE(NULLIF(%s, ''), age_classification),
                    description        = COALESCE(NULLIF(%s, ''), description),
                    thumbnail_url      = COALESCE(NULLIF(%s, ''), thumbnail_url),
                    works_type         = COALESCE(NULLIF(%s, ''), works_type),
                    author = CASE
                        WHEN NULLIF(%s, '') IS NULL          THEN author
                        WHEN COALESCE(author, '') = ''       THEN %s
                        WHEN %s >= ({PRIORITY_CASE_SQL})     THEN %s
                        ELSE author END,
                    illustrator = CASE
                        WHEN NULLIF(%s, '') IS NULL          THEN illustrator
                        WHEN COALESCE(illustrator, '') = ''  THEN %s
                        WHEN %s >= ({PRIORITY_CASE_SQL})     THEN %s
                        ELSE illustrator END,
                    original_author = CASE
                        WHEN NULLIF(%s, '') IS NULL            THEN original_author
                        WHEN COALESCE(original_author, '') = '' THEN %s
                        WHEN %s >= ({PRIORITY_CASE_SQL})       THEN %s
                        ELSE original_author END,
                    genre = CASE
                        WHEN NULLIF(%s, '') IS NULL          THEN genre
                        WHEN COALESCE(genre, '') = ''        THEN %s
                        WHEN %s >= ({PRIORITY_CASE_SQL})     THEN %s
                        ELSE genre END,
                    artist_name = COALESCE(NULLIF(%s, ''), artist_name)
                WHERE works_id = %s
            """, (
                data['age_classification'], data['description'],
                data['thumbnail_url'], data['works_type'],
                data['author'], data['author'], p, data['author'],
                data['illustrator'], data['illustrator'], p, data['illustrator'],
                data['original_author'], data['original_author'], p, data['original_author'],
                data['genre'], data['genre'], p, data['genre'],
                data['artist_name'],
                works_id,
            ))
            changed = cursor.rowcount > 0
        else:
            cursor.execute("""
                INSERT INTO works
                (works_name, artist_name, author, illustrator, original_author,
                 age_classification, description, genre, thumbnail_url, works_type, is_onboarding)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, FALSE)
            """, (
                data['works_name'], data['artist_name'],
                data['author'], data['illustrator'], data['original_author'],
                data['age_classification'], data['description'], data['genre'],
                data['thumbnail_url'], data['works_type'],
            ))
            works_id = cursor.lastrowid
            changed = True

        # works_platform 연동
        if data.get('platform'):
            cursor.execute(
                "INSERT IGNORE INTO works_platform (works_id, platform) VALUES (%s, %s)",
                (works_id, data['platform'])
            )
            changed = changed or cursor.rowcount > 0

        # 해시태그 처리 - 실제 변경된 경우에만 갱신
        if hashtag_list:
            cursor.execute("""
                SELECT h.name FROM hashtag h
                JOIN works_hashtag wh ON h.id = wh.hashtag_id
                WHERE wh.works_id = %s
            """, (works_id,))
            existing_tags = {row[0] for row in cursor.fetchall()}

            if set(hashtag_list) != existing_tags:
                cursor.execute("DELETE FROM works_hashtag WHERE works_id = %s", (works_id,))
                for tag_name in hashtag_list:
                    cursor.execute("""
                        INSERT INTO hashtag (name)
                        VALUES (%s)
                        ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)
                    """, (tag_name,))
                    hashtag_id = cursor.lastrowid
                    cursor.execute(
                        "INSERT IGNORE INTO works_hashtag (works_id, hashtag_id) VALUES (%s, %s)",
                        (works_id, hashtag_id)
                    )
                changed = True

        status = "신규" if not existing else ("업데이트" if changed else "변경없음")
        log_prefix = {
            "신규": "✨ [신규]",
            "업데이트": "🔄 [업데이트]",
            "변경없음": "➖ [변경없음]",
        }[status]

        connection.commit()
        print(f"  {log_prefix} {data['works_name']}")
        safe_save_to_csv(data, status=status)
        return True

    except Exception as e:
        print(f"❌ [DB 에러] {data.get('works_name')} -> {e}")
        try:
            connection.rollback()
        except Exception:
            pass
        backup_failed_row(data, str(e))
        safe_save_to_csv(data, status="실패")
        return False
    finally:
        if lock_acquired:
            try:
                cursor.execute(
                    "DO RELEASE_LOCK(SHA2(CONCAT('works:', %s, '|', %s), 256))",
                    (data['works_name'], data['artist_name']),
                )
            except Exception:
                pass
    

CSV_COLUMNS = [
    "status", "platform", "works_name", "artist_name", "author",
    "illustrator", "original_author", "age_classification",
    "genre", "works_type", "description", "hashtags", "thumbnail_url", "source_url",
]

def save_to_csv(data: dict, status: str = "") -> None:
    """DB 적재 결과를 output_rows.csv 에 항상 기록."""
    file_exists = OUTPUT_CSV.is_file()
    with open(OUTPUT_CSV, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            **data,
            "status": status,
            "hashtags": ", ".join(data.get("hashtags") or []),
        })


def safe_save_to_csv(data: dict, status: str = "") -> None:
    try:
        save_to_csv(data, status=status)
    except Exception as e:
        print(f"⚠️ [CSV 기록 실패] {data.get('works_name')} -> {e}")


def backup_failed_row(data, err_msg):
    file_exists = os.path.isfile(FAILED_CSV)
    with open(FAILED_CSV, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["works_name", "error", "data_dump"])
        writer.writerow([data.get('works_name'), err_msg, str(data)])
