import queue
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import MYSQL_CONFIG, GENRES, BASE_URL, GENRE_MAP
from modules.db_handler import connect_database, save_one_row
from modules.crawler import WebtoonCrawler
from modules.crawler.naver_crawler import NaverCrawler

WORKERS = 3

# 1차 크롤링: 장르별 웹툰
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

        # 리드 로그인 후 워커 풀 초기화 (쿠키 파일 자동 로그인)
        print(f"\n🔧 병렬 워커 {WORKERS}개 초기화 중 (headless)...")
        for i in range(WORKERS):
            w = NaverCrawler(headless=True)
            w.start_driver()
            if w.login_with_cookies():
                workers.append(w)
                print(f"  ✅ 워커 {i+1}/{WORKERS} 준비 완료")
            else:
                print(f"  ⚠️ 워커 {i+1} 쿠키 로그인 실패. 건너뜁니다.")
                w.close_driver()

        if not workers:
            raise Exception("사용 가능한 워커가 없습니다.")

        worker_q = queue.Queue()
        for w in workers:
            worker_q.put(w)

        def crawl_url(args):
            url, genre_code = args
            w = worker_q.get()
            try:
                data = w.crawl_detail_with_retry(url)
                if data and genre_code == '로판':
                    data['genre'] = '로판'
                w.human_pause(1.0, 2.5)
                return data
            except Exception as e:
                print(f"\n⚠️ 워커 오류 ({url}): {e}")
                return None
            finally:
                worker_q.put(w)

        print(f"\n🚀 크롤링 시작 (병렬 워커: {len(workers)}개)")

        with ThreadPoolExecutor(max_workers=len(workers)) as executor:
            futures = {}
            # URL 수집과 크롤링을 겹쳐서 실행: 장르 URL 수집과 동시에 이전 장르 크롤링 진행
            for genre_code in GENRES:
                target_genre_ko = GENRE_MAP.get(genre_code, genre_code)
                print(f"\n=== [URL 수집: {genre_code} ({target_genre_ko})] ===")
                urls = lead.get_genre_urls(BASE_URL + genre_code)
                print(f"📊 {len(urls)}개 URL 큐에 추가 (현재 처리 중: {len(futures)}개)")
                for url in urls:
                    f = executor.submit(crawl_url, (url, genre_code))
                    futures[f] = url

            total = len(futures)
            completed = 0
            for future in as_completed(futures):
                try:
                    data = future.result()
                except Exception as e:
                    print(f"\n⚠️ Future 예외: {e}")
                    data = None
                completed += 1
                print(f"[{completed}/{total}] 결과 처리 중...", end='\r')
                if data and save_one_row(conn, cursor, data):
                    total_saved += 1

        print(f"\n\n✅ 크롤링 완료. 총 {total_saved}개 작품 저장됨.")

    except KeyboardInterrupt:
        print("\n🛑 사용자에 의해 중단되었습니다.")
    except Exception as e:
        print(f"\n❌ 치명적 오류 발생: {e}")
    finally:
        for w in workers:
            w.close_driver()
        if 'lead' in locals():
            lead.close_driver()
        if conn and conn.is_connected():
            cursor.close()
            conn.close()
            print("데이터베이스 연결이 안전하게 종료되었습니다.")

if __name__ == "__main__":
    main()
