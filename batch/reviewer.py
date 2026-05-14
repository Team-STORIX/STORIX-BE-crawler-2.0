import json
from pathlib import Path
from datetime import date
from batch.validator import VALID_PLATFORM, VALID_AGE, VALID_WORKS_TYPE
from config import OUTPUT_DIR


class ManualReviewer:
    def __init__(self):
        self.fixed_records = []
        self.skipped = 0

    def _get_valid_input(self, prompt: str, valid_values: set | None = None, required: bool = False) -> str:
        """사용자 입력을 받고 검증."""
        while True:
            value = input(prompt).strip()
            
            if not value:
                if required:
                    print('   ❌ 필수 필드입니다.')
                    continue
                return ''
            
            if valid_values is not None and value not in valid_values:
                print(f'   ❌ 허용값: {", ".join(sorted(valid_values))}')
                continue
            
            return value

    def _edit_record(self, record: dict, reasons: list[str]) -> dict | None:
        """대화형으로 레코드 필드를 수정."""
        print(f'\n{"="*70}')
        print(f'📝 작품명: {record.get("works_name", "(없음)")}')
        print(f'🔴 오류: {", ".join(reasons)}')
        print(f'{"="*70}')
        
        # 필수 필드
        if not record.get('works_name'):
            record['works_name'] = self._get_valid_input(
                '   작품명 입력: ',
                required=True
            )
        
        if not record.get('artist_name'):
            record['artist_name'] = self._get_valid_input(
                '   작가명 입력 (쉼표 구분): ',
                required=True
            )
        
        if not record.get('platform'):
            print(f'   허용된 플랫폼: {", ".join(sorted(VALID_PLATFORM))}')
            record['platform'] = self._get_valid_input(
                '   플랫폼 선택: ',
                valid_values=VALID_PLATFORM,
                required=True
            )
        
        if not record.get('source_url'):
            record['source_url'] = self._get_valid_input(
                '   소스 URL 입력: ',
                required=True
            )
        
        # 선택 필드
        if not record.get('age_classification'):
            print(f'   허용된 연령: {", ".join(sorted(VALID_AGE - {""}))} (비워도 됨)')
            age = self._get_valid_input(
                '   연령 분류 [선택]: ',
                valid_values=VALID_AGE
            )
            if age:
                record['age_classification'] = age
        
        if not record.get('works_type'):
            print(f'   허용된 유형: {", ".join(sorted(VALID_WORKS_TYPE - {""}))} (비워도 됨)')
            wtype = self._get_valid_input(
                '   작품 유형 [선택]: ',
                valid_values=VALID_WORKS_TYPE
            )
            if wtype:
                record['works_type'] = wtype
        
        if not record.get('description'):
            desc = input('   작품 설명 [선택]: ').strip()
            if desc:
                record['description'] = desc
        
        # 확인
        print(f'\n✨ 수정 결과:')
        print(f'   - 작품명: {record.get("works_name")}')
        print(f'   - 작가명: {record.get("artist_name")}')
        print(f'   - 플랫폼: {record.get("platform")}')
        print(f'   - URL: {record.get("source_url")[:50]}...' if record.get("source_url") else '   - URL: (없음)')
        
        confirm = input('\n이 수정을 저장하시겠습니까? (y/n): ').strip().lower()
        if confirm == 'y':
            return record
        else:
            return None

    def review_and_fix(self, review_queue_path: Path) -> tuple[int, int]:
        """검수큐 파일을 읽고 대화형으로 수정."""
        if not review_queue_path.exists():
            print(f'❌ 검수큐 파일을 찾을 수 없습니다: {review_queue_path}')
            return 0, 0

        total = 0
        fixed = 0
        fixed_records = []

        with open(review_queue_path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                
                total += 1
                record = entry.get('record', {})
                reasons = entry.get('reason', [])
                
                fixed_record = self._edit_record(record, reasons)
                if fixed_record:
                    fixed += 1
                    fixed_records.append(fixed_record)
                else:
                    self.skipped += 1
                    print('⏭️  건너뜀\n')

        # 수정된 레코드를 별도 파일로 저장
        if fixed_records:
            output_path = review_queue_path.parent / 'fixed_records.jsonl'
            with open(output_path, 'w', encoding='utf-8') as f:
                for rec in fixed_records:
                    f.write(json.dumps(rec, ensure_ascii=False) + '\n')
            print(f'\n✅ {fixed}건의 수정된 레코드가 저장되었습니다: {output_path}')
            print(f'💡 다음 명령어로 DB에 적재하세요:')
            print(f'   python cli.py batch import --input {output_path}')
        
        return total, fixed


def run_review_fix(input_path: str | None = None) -> None:
    """검수큐 대화형 수정 실행."""
    # input_path가 없으면 당일 검수큐 자동 감지
    if input_path:
        review_queue_path = Path(input_path)
    else:
        today_folder = OUTPUT_DIR / date.today().strftime('%Y-%m-%d')
        review_queue_path = today_folder / 'manual_review_queue.jsonl'
        if not today_folder.exists():
            print(f'❌ 당일 폴더가 없습니다: {today_folder}')
            return
        print(f'📂 당일 검수큐: {review_queue_path}')
    
    reviewer = ManualReviewer()
    total, fixed = reviewer.review_and_fix(review_queue_path)
    
    print(f'\n📊 결과: {fixed}/{total}건 수정, {reviewer.skipped}건 건너뜀')
