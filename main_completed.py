import time
import queue
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from config import MYSQL_CONFIG, COMPLETED_URL
from modules.db_handler import connect_database, save_one_row
from modules.crawler import WebtoonCrawler
from modules.crawler.naver_crawler import NaverCrawler

TARGET_LIMIT = 1000
WORKERS = 3

def scroll_and_collect_urls(driver, limit=1000):
    """
    Footer가 커도 확실하게 로딩하는 '바닥 찍고 흔들기' 전략
    """
    print(f"📜 목표 {limit}개까지 스크롤을 시작합니다...")
    item_xpath = "//div[@id='content']/div[1]/ul/li/a"

    prev_count = len(driver.find_elements(By.XPATH, item_xpath))
    stuck_count = 0

    while True:
        if prev_count >= limit:
            print(f"\n✅ 목표 개수 도달 ({prev_count}개). 스크롤 중단.")
            break

        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1.0)

        elems = driver.find_elements(By.XPATH, item_xpath)
        curr_count = len(elems)

        print(f"  └─ 현재 {curr_count}개 로딩됨...", end='\r')

        if curr_count > prev_count:
            prev_count = curr_count
            stuck_count = 0
        else:
            stuck_count += 1

            if stuck_count >= 3:
                print(f"\n⚠️ 더 이상 로딩되지 않습니다. (총 {curr_count}개)")
                break

            driver.execute_script("window.scrollBy(0, -1500);")
            time.sleep(0.7)
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1.5)

    urls = []
    elems = driver.find_elements(By.XPATH, item_xpath)
    for el in elems[:limit]:
        href = el.get_attribute('href')
        if href and 'titleId=' in href:
            urls.append(href)

    return list(dict.fromkeys(urls))


def crawl_urls_parallel(urls, worker_q, n_workers):
    """워커 풀로 URL 목록을 병렬 크롤링하여 결과 리스트 반환."""
    if not urls:
        return []

    def crawl_one(url):
        w = worker_q.get()
        try:
            return w.crawl_detail_with_retry(url)
        except Exception as e:
            print(f"\n⚠️ 워커 오류 ({url}): {e}")
            return None
        finally:
            w.human_pause(1.0, 2.0)
            worker_q.put(w)

    results = []
    with ThreadPoolExecutor(max_workers=n_workers) as executor:
        total = len(urls)
        for i, data in enumerate(executor.map(crawl_one, urls), 1):
            print(f"[{i}/{total}] 진행 중...", end='\r')
            if data:
                results.append(data)

    return results


def main():
    conn = connect_database(MYSQL_CONFIG)
    if not conn: return
    cursor = conn.cursor()

    lead = WebtoonCrawler()
    workers = []
    total_saved = 0

    try:
        lead.start_driver()
        if not lead.login(): return

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

        print(f"\n🚀 [완결 웹툰] 페이지로 이동합니다: {COMPLETED_URL}")
        lead.driver.get(COMPLETED_URL)
        time.sleep(2)

        # === [1단계] 인기순 ===
        print("\n=== [1단계] 인기순 정렬 선택 ===")
        try:
            btn_popular = WebDriverWait(lead.driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, '//*[@id="content"]/div[1]/div/div[2]/button[1]'))
            )
            btn_popular.click()
            time.sleep(2)

            popular_urls = scroll_and_collect_urls(lead.driver, limit=TARGET_LIMIT)
            print(f"📊 [인기순] 수집 대상: {len(popular_urls)}개")

            for data in crawl_urls_parallel(popular_urls, worker_q, len(workers)):
                if save_one_row(conn, cursor, data):
                    total_saved += 1

        except Exception as e:
            print(f"❌ [인기순] 진행 중 오류 발생: {e}")
            traceback.print_exc()

        # === [2단계] 별점순 ===
        print("\n\n=== [2단계] 별점순 정렬 선택 ===")
        try:
            print("🔄 페이지를 새로고침하여 상태를 초기화합니다...")
            lead.driver.get(COMPLETED_URL)
            time.sleep(3)

            print("👉 '별점순' 버튼 클릭 시도...")
            btn_star = WebDriverWait(lead.driver, 15).until(
                EC.element_to_be_clickable((By.XPATH, '//*[@id="content"]/div[1]/div/div[2]/button[4]'))
            )
            btn_star.click()
            time.sleep(2)

            star_urls = scroll_and_collect_urls(lead.driver, limit=TARGET_LIMIT)
            print(f"📊 [별점순] 수집 대상: {len(star_urls)}개")

            for data in crawl_urls_parallel(star_urls, worker_q, len(workers)):
                if save_one_row(conn, cursor, data):
                    total_saved += 1

        except Exception as e:
            print(f"❌ [별점순] 진행 중 오류 발생: {e}")
            traceback.print_exc()

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
            conn.close()
            print(f"\n🎉 [완결 웹툰] 작업 종료. 총 {total_saved}개 저장됨.")

if __name__ == "__main__":
    main()
