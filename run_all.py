"""
전체 크롤링 오케스트레이터.
- 네이버: main.py → main_daily_plus.py → main_completed.py 순차 실행
- 카카오: main_kakao_popular.py 별도 실행
- 네이버 그룹과 카카오를 동시에 실행하여 총 소요 시간을 단축.

사용법:
    python3 run_all.py          # 네이버 + 카카오 동시 실행
    python3 run_all.py naver    # 네이버만 실행
    python3 run_all.py kakao    # 카카오만 실행
"""
import sys
import subprocess
import threading
import time

NAVER_SCRIPTS = ["main.py", "main_daily_plus.py", "main_completed.py"]
KAKAO_SCRIPTS = ["main_kakao_popular.py"]


def run_scripts_sequentially(scripts, label):
    for script in scripts:
        print(f"\n{'='*50}")
        print(f"[{label}] 시작: {script}")
        print(f"{'='*50}")
        result = subprocess.run([sys.executable, script])
        if result.returncode != 0:
            print(f"⚠️ [{label}] {script} 비정상 종료 (code={result.returncode}). 다음 스크립트로 계속...")
    print(f"\n✅ [{label}] 모든 스크립트 완료.")


def main():
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "all"

    start = time.time()

    if mode == "naver":
        run_scripts_sequentially(NAVER_SCRIPTS, "NAVER")
    elif mode == "kakao":
        run_scripts_sequentially(KAKAO_SCRIPTS, "KAKAO")
    else:
        # 네이버 그룹과 카카오를 스레드로 동시 실행
        # 각 그룹은 내부적으로 독립적인 subprocess를 실행하므로 로그인 세션이 분리됨.
        naver_thread = threading.Thread(
            target=run_scripts_sequentially,
            args=(NAVER_SCRIPTS, "NAVER"),
            daemon=True
        )
        kakao_thread = threading.Thread(
            target=run_scripts_sequentially,
            args=(KAKAO_SCRIPTS, "KAKAO"),
            daemon=True
        )

        print("🚀 [전체 크롤링] 네이버 + 카카오 동시 시작")
        print("   각 그룹별 로그인 창이 열립니다.")

        naver_thread.start()
        kakao_thread.start()

        naver_thread.join()
        kakao_thread.join()

    elapsed = time.time() - start
    minutes, seconds = divmod(int(elapsed), 60)
    print(f"\n🎉 전체 크롤링 완료. 소요 시간: {minutes}분 {seconds}초")


if __name__ == "__main__":
    main()
