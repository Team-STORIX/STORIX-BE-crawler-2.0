#!/usr/bin/env bash
# 로그인 쿠키를 크롤러 서버용 S3 에 올린다 (#77). 서버는 다음 부팅 때 S3 쪽이 더 새 것만 받는다.
# 쿠키를 S3 에 올리는 곳은 이 스크립트 하나다 — 서버(deploy/nightly.sh)도 끄기 전에 --newer 로 이걸 부른다 (#122)
#
# 세션 만료 슬랙 알림이 오면:
#   python cli.py login --platform kakao_page
#   deploy/push_sessions.sh kakao            # sessions/kakao_cookies.pkl 만
#   deploy/push_sessions.sh                  # sessions/*.pkl 전부
#   deploy/push_sessions.sh --newer          # S3 쪽보다 새 파일만 (서버용, GNU date · stat)
#
# 만료된 플랫폼만 올린다. 전부 올리면 서버가 실행 중 다시 저장한 다른 플랫폼 쿠키를 옛 것으로 덮을 수 있다.
# 하나라도 못 올리면 종료 코드 1.
set -uo pipefail
cd "$(dirname "$0")/.."

BUCKET=${SESSIONS_BUCKET:-storix-crawler-sessions}
newer=
if [ "${1:-}" = --newer ]; then
  newer=1
  shift
fi

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

failed=0
for f in "${files[@]}"; do
  [ -f "$f" ] || continue
  key="sessions/${f##*/}"
  if [ -n "$newer" ]; then
    # S3 쪽이 더 새 것(그사이 로컬에서 올림)은 덮지 않는다
    modified=$(aws s3api head-object --bucket "$BUCKET" --key "$key" --query LastModified --output text 2>/dev/null)
    remote=$( [ -n "$modified" ] && date -d "$modified" +%s || echo 0 )
    [ "$(stat -c %Y "$f")" -gt "$remote" ] || continue
  fi
  if aws s3 cp --only-show-errors --sse AES256 "$f" "s3://$BUCKET/$key"; then
    echo "⬆️  ${f##*/} ($(date -r "$f" '+%m-%d %H:%M') 저장분)"
  else
    echo "❌ ${f##*/} 못 올림"
    failed=1
  fi
done
[ -n "$newer" ] || echo "다음 자정 실행부터 서버가 받습니다"
exit "$failed"
