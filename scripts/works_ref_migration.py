"""works 행 병합/삭제 시 works_id 를 참조하는 서비스 테이블 이전 공용 로직.

works_platform/works_hashtag 외에도 서비스 DB에는 works_id 를 참조하는
테이블(토픽룸, 즐겨찾기, 리뷰 등)이 있어서, works 행을 삭제하기 전에
참조를 keeper 로 옮기지 않으면 고아 참조가 남아 API 가 NPE 로 터진다.
(2026-07-18 topic_room NPE 장애의 원인)

사용:
    from works_ref_migration import migrate_service_refs
    ...
    if not migrate_service_refs(wcur, keep_id, drop_id):
        continue  # 충돌 잔여 → works 행을 삭제하지 말 것
"""

# (테이블, 유니크 충돌로 못 옮긴 행 처리)
#   'delete' = 중복이므로 삭제해도 안전 (같은 유저가 keeper 도 이미 참조 중)
#   'block'  = 삭제하면 사용자 데이터 유실 → 병합을 중단하고 수동 확인
SERVICE_REF_TABLES = [
    ('user_genre_score_log', 'delete'),
    ('user_favorite_works', 'delete'),
    ('reader_board', 'block'),
    ('review', 'block'),
    ('taste_exploration', 'block'),
    ('topic_room', 'block'),
]


def is_onboarding_row(cursor, works_id: int) -> bool:
    """works_id 가 is_onboarding=TRUE 인지 확인. 병합 과정에서 이 행은 절대 삭제 금지."""
    cursor.execute("SELECT is_onboarding FROM works WHERE works_id=%s", (works_id,))
    row = cursor.fetchone()
    return bool(row and row[0])


def migrate_service_refs(cursor, keep_id: int, drop_id: int, *, allow_onboarding_drop: bool = False) -> bool:
    """drop_id 를 참조하는 서비스 테이블 행을 keep_id 로 이전.

    True  → 이전 완료, works 행 삭제 가능
    False → 유니크 충돌 잔여('block' 테이블) 또는 drop_id 가 is_onboarding=TRUE
            → works 행을 삭제하면 안 됨

    is_onboarding 보호: 기본(allow_onboarding_drop=False)은 drop_id 가
    is_onboarding=TRUE 면 병합 자체를 스킵 — merge_split_works/merge_subset_artists/
    fix_phantom_artists/fix_role_label_artists 처럼 사람 판단 없이 자동으로 keeper 를
    고르는 스크립트용. review_similar_works.py 처럼 사람이 각 쌍을 직접 검토해
    병합을 선택한 경우는 allow_onboarding_drop=True 로 호출 — 대신 호출부에서
    keeper 의 is_onboarding 을 TRUE 로 강제해 온보딩 상태가 유실되지 않게 한다.
    """
    if not allow_onboarding_drop and is_onboarding_row(cursor, drop_id):
        print(f"  🛡️  works #{drop_id}: is_onboarding=TRUE — 병합/삭제 대상에서 절대 보호, 스킵")
        return False
    ok = True
    for table, on_conflict in SERVICE_REF_TABLES:
        cursor.execute(
            f"UPDATE IGNORE {table} SET works_id=%s WHERE works_id=%s",
            (keep_id, drop_id),
        )
        cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE works_id=%s", (drop_id,))
        left = cursor.fetchone()[0]
        if not left:
            continue
        if on_conflict == 'delete':
            cursor.execute(f"DELETE FROM {table} WHERE works_id=%s", (drop_id,))
        else:
            print(f"  ⚠️ works #{drop_id}: {table} 잔여 {left}건 "
                  f"(keeper #{keep_id}와 유니크 충돌) — 병합 스킵, 수동 확인 필요")
            ok = False
    return ok
