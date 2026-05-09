import mysql.connector
from mysql.connector import Error as MySQLError
import csv
import os
from config import FAILED_CSV

# 장르 우선순위
GENRE_PRIORITY = {
    '로판': 5,
    '무협/사극': 1,
    '판타지': 1,
    '액션': 1,
    '드라마': 1,
    '로맨스': 1,
    '일상': 1,
    '개그': 1,
    '스릴러': 1,
}

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
    genre = data.get('genre', '').strip().lstrip('#')
    genre = genre.replace('무협 / 사극', '무협/사극')
    
    # 연령
    age_raw = data.get('age_classification', '').replace(' ', '')
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
    author, illustrator, original_author = parse_artists(data.get('artist_name', ''))

    return {
        **data,
        'genre': genre,
        'age_classification': age,
        'author': author,
        'illustrator': illustrator,
        'original_author': original_author,
        'priority': GENRE_PRIORITY.get(genre, 0)
    }


def save_one_row(connection, cursor, raw_data):
    hashtag_list = raw_data.get('hashtags', [])
    data = normalize_data(raw_data)
    
    priority_case = """
        CASE genre
            WHEN '로판' THEN 5
            WHEN '판타지' THEN 1
            WHEN '무협/사극' THEN 1
            WHEN '로맨스' THEN 1
            WHEN '일상' THEN 1
            WHEN '개그' THEN 1
            WHEN '스릴러' THEN 1
            ELSE 0
        END
    """
    works_sql = f"""
    INSERT INTO works
    (platform, works_name, artist_name, author, illustrator, original_author,
     age_classification, description, genre, thumbnail_url, works_type)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        artist_name = CASE WHEN %s >= ({priority_case}) THEN VALUES(artist_name) ELSE artist_name END,
        author      = CASE WHEN %s >= ({priority_case}) THEN VALUES(author)      ELSE author END,
        illustrator = CASE WHEN %s >= ({priority_case}) THEN VALUES(illustrator) ELSE illustrator END,
        original_author = CASE WHEN %s >= ({priority_case}) THEN VALUES(original_author) ELSE original_author END,
        age_classification = VALUES(age_classification),
        description    = VALUES(description),
        thumbnail_url  = VALUES(thumbnail_url),
        works_type     = VALUES(works_type),
        genre = CASE WHEN %s >= ({priority_case}) THEN VALUES(genre) ELSE genre END
    """
    p = data['priority']
    works_vals = (
        data['platform'], data['works_name'], data['artist_name'],
        data['author'], data['illustrator'], data['original_author'],
        data['age_classification'], data['description'], data['genre'],
        data['thumbnail_url'], data['works_type'],
        p, p, p, p, p  # artist_name, author, illustrator, original_author, genre
    )

    try:
        # works 테이블 저장 
        cursor.execute(works_sql, works_vals)
        affected_rows = cursor.rowcount

        while cursor.nextset(): pass


        # 방금 저장한 works의 works_id
        cursor.execute("SELECT works_id FROM works WHERE works_name = %s", (data['works_name'],))
        work_id_result = cursor.fetchone()
        
        if not work_id_result:
            raise Exception(f"Failed to retrieve works_id for {data['works_name']}")
        works_id = work_id_result[0]

        while cursor.nextset(): pass

        # 해시태그 처리 
        if hashtag_list:
            cursor.execute("DELETE FROM works_hashtag WHERE works_id = %s", (works_id,))
            for tag_name in hashtag_list:
                if not tag_name: continue
                
                cursor.execute("SELECT id FROM hashtag WHERE name = %s", (tag_name,))
                hashtag_result = cursor.fetchone()

                while cursor.nextset(): pass
                
                if hashtag_result:
                    hashtag_id = hashtag_result[0]
                else:
                    cursor.execute("INSERT INTO hashtag (name) VALUES (%s)", (tag_name,))
                    while cursor.nextset(): pass
                    hashtag_id = cursor.lastrowid
                
                cursor.execute(
                    "INSERT IGNORE INTO works_hashtag (works_id, hashtag_id) VALUES (%s, %s)",
                    (works_id, hashtag_id)
                )
                while cursor.nextset(): pass

        connection.commit()
        
        if affected_rows == 1:
            log_prefix = "✨ [신규]"
        elif affected_rows == 2:
            log_prefix = "🔄 [업데이트]"
        else:
            log_prefix = "➖ [변경없음]"

        print(f"  {log_prefix} {data['works_name']}")
        return True
    
    except Exception as e:
        print(f"❌ [DB 에러] {data.get('works_name')} -> {e}")
        connection.rollback()
        backup_failed_row(data, str(e))
        return False
    

def backup_failed_row(data, err_msg):
    file_exists = os.path.isfile(FAILED_CSV)
    with open(FAILED_CSV, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["works_name", "error", "data_dump"])
        writer.writerow([data.get('works_name'), err_msg, str(data)])