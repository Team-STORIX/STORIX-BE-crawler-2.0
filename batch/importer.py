import json
import time
from pathlib import Path
from datetime import date

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
    def __init__(self, conn, cursor, verbose: bool = False):
        self._conn = conn
        self._cursor = cursor
        self._verbose = verbose
        self._error_sample: list[tuple[str, list[str]]] = []  # (works_name, errors)

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
        record, _ = fallback.try_resolve(record)
        is_valid, errors = validate(record)

        if not is_valid:
            if self._verbose or len(self._error_sample) < 5:
                name = record.get('works_name', '(제목없음)')
                self._error_sample.append((name, errors))
                if self._verbose:
                    print(f'    ⚠️  검수큐 [{name}]: {" | ".join(errors)}')
            fallback.queue_for_review(record, errors, review_path)
            stats.reviewed += 1
            return

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
            files = sorted(path.rglob('*.jsonl'))
            files = [
                f for f in files
                if f.name not in {'manual_review_queue.jsonl', 'platform_status.jsonl'}
            ]
        else:
            raise FileNotFoundError(f'경로를 찾을 수 없습니다: {path}')

        if not files:
            print(f'⚠️  적재할 JSONL 파일이 없습니다: {path}')
            return stats

        print(f'📦 적재 시작: {len(files)}개 파일')
        for f in files:
            self.import_file(f, stats)

        if self._error_sample and not self._verbose:
            print(f'\n⚠️  검수큐 원인 샘플 (최대 5건):')
            for name, errors in self._error_sample:
                print(f'   [{name}] {" | ".join(errors)}')
            print('   → 전체 확인: python cli.py batch import --input <경로> --verbose')

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


def run_import(input_path: str, watch: bool = False, verbose: bool = False) -> None:
    conn = connect_database(MYSQL_CONFIG)
    if not conn:
        raise RuntimeError('DB 연결 실패')

    cursor = conn.cursor()
    importer = BatchImporter(conn, cursor, verbose=verbose)

    try:
        if watch:
            watch_dir = Path(input_path) if input_path else OUTPUT_DIR
            importer.watch(watch_dir)
        else:
            # input_path가 없으면 당일 폴더만 자동 감지
            if input_path:
                path = Path(input_path)
            else:
                today_folder = OUTPUT_DIR / date.today().strftime('%Y-%m-%d')
                if not today_folder.exists():
                    print(f'⚠️  당일 폴더가 없습니다: {today_folder}')
                    return
                path = today_folder
                print(f'📂 당일 폴더 자동 감지: {path}')

            stats = importer.import_path(path)
            print(f'\n✅ 적재 완료 → {stats}')
    finally:
        cursor.close()
        if conn.is_connected():
            conn.close()
