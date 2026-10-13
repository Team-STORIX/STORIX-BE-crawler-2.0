#!/usr/bin/env bash
# 로컬 로그인 쿠키를 크롤러 서버용 S3 에 올린다 (#77). 서버는 다음 부팅 때 S3 쪽이 더 새 것만 받는다.
#
# 세션 만료 슬랙 알림이 오면:
#   python cli.py login --platform kakao_page
#   deploy/push_sessions.sh kakao            # sessions/kakao_cookies.pkl 만
#   deploy/push_sessions.sh                  # sessions/*.pkl 전부
#
# 만료된 플랫폼만 올린다. 전부 올리면 서버가 실행 중 다시 저장한 다른 플랫폼 쿠키를 옛 것으로 덮을 수 있다.
set -euo pipefail
cd "$(dirname "$0")/.."

BUCKET=${SESSIONS_BUCKET:-storix-crawler-sessions}

if [ $# -eq 0 ]; then
  files=(sessions/*_cookies.pkl)
else
  files=()
  for name in "$@"; do
    f="sessions/${name%_cookies.pkl}_cookies.pkl"
    [ -f "$f" ] || { echo "❌ $f 없음 (먼저 python cli.py login 으로 만든다)"; exit 1; }
    files+=("$f")
  done
fi

for f in "${files[@]}"; do
  [ -f "$f" ] || continue
  aws s3 cp --only-show-errors --sse AES256 "$f" "s3://$BUCKET/sessions/${f##*/}"
  echo "⬆️  ${f##*/} ($(date -r "$f" '+%m-%d %H:%M') 저장분)"
done
echo "다음 자정 실행부터 서버가 받습니다"
