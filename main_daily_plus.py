import time
import queue
import traceback
from concurrent.futures import ThreadPoolExecutor
from selenium.common.exceptions import InvalidSessionIdException, WebDriverException
from config import MYSQL_CONFIG, DAILY_PLUS_URL
from modules.db_handler import connect_database, save_one_row
from modules.crawler import WebtoonCrawler
from modules.crawler.naver_crawler import NaverCrawler

WORKERS = 3

# 2차 크롤링: 매일+
def main():
    conn = connect_database(MYSQL_CONFIG)
    if not conn:
        return
    cursor = conn.cursor()

    lead = WebtoonCrawler()
    workers = []
    total_saved = 0

    try:
        lead.start_driver()
        if not lead.login():
            raise Exception("로그인 실패로 프로그램을 종료합니다.")

        print("\n🚀 [매일+] 크롤링을 시작합니다...")

        # URL 수집 (세션 끊김 재시도 포함)
        target_urls = []
        while True:
            try:
                if not lead.driver:
                    print("🔄 브라우저 재시작 중...")
                    lead.start_driver()
                    lead.login()

                target_urls = lead.get_genre_urls(DAILY_PLUS_URL)
                print(f"📊 [매일+] 수집 대상: 총 {len(target_urls)}개 작품")
                break

            except (InvalidSessionIdException, WebDriverException):
                print("⚠️ [오류] 목록 수집 중 세션 끊김. 5초 후 재시도...")
                lead.close_driver()
                time.sleep(5)
                continue

        # 워커 풀 초기화
        print(f"\n🔧 병렬 워커 {WORKERS}개 초기화 중 (headless)...")
        for i in range(WORKERS):
            w = NaverCrawler(headless=True)
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
            except (InvalidSessionIdException, WebDriverException) as e:
                print(f"\n⚠️ 워커 세션 오류 ({url}): {e}")
                return None
            except Exception as e:
                print(f"\n⚠️ 워커 오류 ({url}): {e}")
                return None
            finally:
                w.human_pause(1.0, 2.5)
                worker_q.put(w)

        total = len(target_urls)
        with ThreadPoolExecutor(max_workers=len(workers)) as executor:
            for i, data in enumerate(executor.map(crawl_one, target_urls), 1):
                print(f"[{i}/{total}] 진행 중...", end='\r')
                if data and save_one_row(conn, cursor, data):
                    total_saved += 1

    except KeyboardInterrupt:
        print("\n🛑 사용자에 의해 중단되었습니다.")
    except Exception as e:
        traceback.print_exc()
        print(f"\n❌ 치명적 오류 발생: {e}")
    finally:
        for w in workers:
            w.close_driver()
        if 'lead' in locals():
            lead.close_driver()
        if conn and conn.is_connected():
            cursor.close()
            conn.close()
            print(f"🎉 [매일+] 작업 종료. 총 {total_saved}개 작품 처리 완료.")

if __name__ == "__main__":
    main()
