import json
import time
from pathlib import Path

from modules.db_handler import connect_database, save_one_row
from config import MYSQL_CONFIG, OUTPUT_DIR
from batch.validator import validate
from batch import fallback


class ImportStats:
    def __init__(self):
        self.success = 0
        self.failed = 0
        self.skipped = 0
        self.reviewed = 0

    def __str__(self):
        return (
            f'성공 {self.success}건 | 실패 {self.failed}건 | '
            f'스킵 {self.skipped}건 | 검수큐 {self.reviewed}건'
        )


class BatchImporter:
    def __init__(self, conn, cursor):
        self._conn = conn
        self._cursor = cursor

    def _review_queue_path(self, base_dir: Path) -> Path:
        return base_dir / 'manual_review_queue.jsonl'

    def import_file(self, path: Path, stats: ImportStats | None = None) -> ImportStats:
        if stats is None:
            stats = ImportStats()

        review_path = self._review_queue_path(path.parent)
        total = 0

        with open(path, encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    stats.skipped += 1
                    continue

                total += 1
                self._process_record(record, stats, review_path)

        print(f'  📄 {path.name}: {total}건 처리 → {stats}')
        return stats

    def _process_record(self, record: dict, stats: ImportStats, review_path: Path) -> None:
        record, resolvable = fallback.try_resolve(record)
        is_valid, errors = validate(record)

        if not is_valid:
            if not resolvable:
                fallback.queue_for_review(record, errors, review_path)
                stats.reviewed += 1
                return
            print(f'  ⚠️  검증 경고 ({record.get("works_name")}): {errors}')

        ok = save_one_row(self._conn, self._cursor, record)
        if ok:
            stats.success += 1
        else:
            stats.failed += 1

    def import_path(self, path: Path) -> ImportStats:
        stats = ImportStats()

        if path.is_file():
            files = [path]
        elif path.is_dir():
            files = sorted(path.glob('*.jsonl'))
            files = [f for f in files if f.name != 'manual_review_queue.jsonl']
        else:
            raise FileNotFoundError(f'경로를 찾을 수 없습니다: {path}')

        if not files:
            print(f'⚠️  적재할 JSONL 파일이 없습니다: {path}')
            return stats

        print(f'📦 적재 시작: {len(files)}개 파일')
        for f in files:
            self.import_file(f, stats)

        return stats

    def watch(self, watch_dir: Path, interval: int = 30) -> None:
        print(f'👁️  감시 모드 시작: {watch_dir} (체크 간격 {interval}초)')
        print('   Ctrl+C로 종료')
        seen: set[Path] = set(watch_dir.rglob('*.jsonl'))

        try:
            while True:
                time.sleep(interval)
                current = set(watch_dir.rglob('*.jsonl'))
                new_files = sorted(
                    f for f in (current - seen)
                    if f.name != 'manual_review_queue.jsonl'
                )
                seen = current

                for f in new_files:
                    print(f'\n🆕 새 파일 감지: {f}')
                    stats = ImportStats()
                    self.import_file(f, stats)
                    print(f'   결과: {stats}')

        except KeyboardInterrupt:
            print('\n🛑 감시 모드 종료')


def run_import(input_path: str, watch: bool = False) -> None:
    conn = connect_database(MYSQL_CONFIG)
    if not conn:
        raise RuntimeError('DB 연결 실패')

    cursor = conn.cursor()
    importer = BatchImporter(conn, cursor)

    try:
        if watch:
            watch_dir = Path(input_path) if input_path else OUTPUT_DIR
            importer.watch(watch_dir)
        else:
            path = Path(input_path)
            stats = importer.import_path(path)
            print(f'\n✅ 적재 완료 → {stats}')
    finally:
        cursor.close()
        if conn.is_connected():
            conn.close()
