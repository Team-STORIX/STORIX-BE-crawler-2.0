import queue
from concurrent.futures import ThreadPoolExecutor
from config import MYSQL_CONFIG, TOP_300_URL
from modules.db_handler import connect_database, save_one_row
from modules.crawler.kakao_crawler import KakaoCrawler

WORKERS = 3

def main():
    conn = connect_database(MYSQL_CONFIG)
    if not conn: return
    cursor = conn.cursor()

    lead = KakaoCrawler()
    workers = []
    total_saved = 0

    try:
        lead.start_driver()
        if not lead.login(): return

        urls = lead.get_list_urls(TOP_300_URL)
        print(f"\n📊 [카카오페이지] 수집 대상: {len(urls)}개")

        # 워커 풀 초기화 (쿠키 파일 자동 로그인)
        print(f"\n🔧 병렬 워커 {WORKERS}개 초기화 중 (headless)...")
        for i in range(WORKERS):
            w = KakaoCrawler(headless=True)
            w.start_driver()
            if w.login_with_cookies():
                workers.append(w)
                print(f"  ✅ 워커 {i+1}/{WORKERS} 준비 완료")
            else:
                print(f"  ⚠️ 워커 {i+1} 쿠키 로그인 실패.")
                w.close_driver()

        if not workers:
            raise Exception("사용 가능한 워커가 없습니다.")

        worker_q = queue.Queue()
        for w in workers:
            worker_q.put(w)

        def crawl_one(url):
            w = worker_q.get()
            try:
                return w.crawl_detail_with_retry(url)
            except Exception as e:
                print(f"\n⚠️ 워커 오류 ({url}): {e}")
                return None
            finally:
                w.human_pause(1.0, 2.5)
                worker_q.put(w)

        total = len(urls)
        with ThreadPoolExecutor(max_workers=len(workers)) as executor:
            for i, data in enumerate(executor.map(crawl_one, urls), 1):
                print(f"[{i}/{total}] 진행 중...", end='\r')
                if data and save_one_row(conn, cursor, data):
                    total_saved += 1

        print(f"\n✅ [카카오페이지] 완료. 총 {total_saved}개 저장됨.")

    except KeyboardInterrupt:
        print("\n🛑 중단됨")
    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
    finally:
        for w in workers:
            w.close_driver()
        if 'lead' in locals():
            lead.close_driver()
        if conn and conn.is_connected():
            cursor.close()
            conn.close()
            print(f"🎉 [카카오 TOP 300] 작업 종료. 총 {total_saved}개 작품 처리 완료.")

if __name__ == "__main__":
    main()
