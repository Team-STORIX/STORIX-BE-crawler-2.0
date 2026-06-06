import mysql.connector
from mysql.connector import Error as MySQLError
import csv
import os
from config import FAILED_CSV, OUTPUT_CSV

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

        has_author = '글' in part or '각색' in part
        has_illustrator = '그림' in part
        has_original = '원작' in part
        
        # 이름만 추출
        name = part.replace('원작', '').replace('글', '').replace('각색', '').replace('그림', '').strip()

        if not name:
            continue

        if not has_author and not has_illustrator and not has_original:
            if not author:
                author = name
            elif not illustrator:
                illustrator = name
        
        if has_author: author = name
        if has_illustrator: illustrator = name
        if has_original: original_author = name

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
        status = "신규"

        if existing:
            works_id = existing[0]
            cursor.execute(f"""
                UPDATE works SET
                    age_classification = COALESCE(NULLIF(%s, ''), age_classification),
                    description        = COALESCE(NULLIF(%s, ''), description),
                    thumbnail_url      = COALESCE(NULLIF(%s, ''), thumbnail_url),
                    works_type         = COALESCE(NULLIF(%s, ''), works_type),
                    author             = CASE WHEN %s IS NOT NULL AND %s >= ({PRIORITY_CASE_SQL}) THEN %s ELSE author END,
                    illustrator        = CASE WHEN %s IS NOT NULL AND %s >= ({PRIORITY_CASE_SQL}) THEN %s ELSE illustrator END,
                    original_author    = CASE WHEN %s IS NOT NULL AND %s >= ({PRIORITY_CASE_SQL}) THEN %s ELSE original_author END,
                    genre              = CASE WHEN %s <> '' AND %s >= ({PRIORITY_CASE_SQL}) THEN %s ELSE genre END
                WHERE works_id = %s
            """, (
                data['age_classification'], data['description'],
                data['thumbnail_url'], data['works_type'],
                data['author'], p, data['author'],
                data['illustrator'], p, data['illustrator'],
                data['original_author'], p, data['original_author'],
                data['genre'], p, data['genre'],
                works_id,
            ))
            changed = cursor.rowcount > 0
            status = "업데이트" if changed else "변경없음"
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

        # 해시태그 처리
        if hashtag_list:
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

        if existing:
            status = "업데이트" if changed else "변경없음"

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
        connection.rollback()
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
