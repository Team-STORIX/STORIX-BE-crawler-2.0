#!/usr/bin/env bash
# 크롤러 서버 야간 실행 (#77). 부팅 때 systemd(storix-crawler-nightly.service)가 한 번 부른다.
#   1) Parameter Store /storix/crawler/ → 환경변수 파일 (메모리 위 /run, 디스크에 안 남김)
#   2) main 을 받아 바뀌었으면 이미지를 다시 빌드
#   3) 노션 작품 요청 처리 (cli.py requests --env prod). 세션 만료 · 실패 알림은 cli 가 슬랙으로 보낸다
#   4) 서버 종료 — /etc/storix-crawler/hold 가 있으면 끄지 않는다 (들어가서 고칠 때)
set -uo pipefail

# 아래에서 git 이 이 파일을 바꾸므로, 실행 중인 파일이 바뀌지 않게 사본으로 다시 시작한다
if [ -z "${NIGHTLY_COPY:-}" ]; then
  cp "$0" /run/storix-crawler-nightly.sh && NIGHTLY_COPY=1 exec bash /run/storix-crawler-nightly.sh
fi

APP_DIR=${APP_DIR:-/opt/storix-crawler}
PARAM_PATH=${PARAM_PATH:-/storix/crawler/}
DEPLOY_REF=${DEPLOY_REF:-main}  # 서버에 갈 브랜치. 릴리스 전 코드가 가지 않게 main
ENV_FILE=/run/storix-crawler.env
HOLD_FILE=/etc/storix-crawler/hold

log() { echo "[nightly] $*"; }

# cli 까지 못 가고 죽으면 cli 가 알릴 수 없으니 여기서 알린다
slack() {
  local token channel
  token=$(grep -s '^SLACK_BOT_TOKEN=' "$ENV_FILE" | cut -d= -f2-)
  channel=$(grep -s '^SLACK_CHANNEL_ID=' "$ENV_FILE" | cut -d= -f2-)
  [ -n "$token" ] && [ -n "$channel" ] || return 0
  curl -s -o /dev/null -X POST https://slack.com/api/chat.postMessage \
    -H "Authorization: Bearer $token" -H 'Content-Type: application/json; charset=utf-8' \
    --data "$(python3 -c 'import json,sys; print(json.dumps({"channel": sys.argv[1], "text": sys.argv[2]}))' "$channel" "$1")"
}

finish() {
  rm -f "$ENV_FILE"
  if [ -e "$HOLD_FILE" ]; then
    log "$HOLD_FILE 가 있어 서버를 끄지 않습니다"
  else
    log '서버 종료'
    shutdown -h now
  fi
}
trap finish EXIT

cd "$APP_DIR" || { log "$APP_DIR 없음"; exit 1; }

# 1) 비밀값. 이름이 곧 환경변수 이름이다 (/storix/crawler/NOTION_TOKEN → NOTION_TOKEN)
umask 077
if ! aws ssm get-parameters-by-path --path "$PARAM_PATH" --with-decryption --recursive \
     --query 'Parameters[].[Name,Value]' --output text \
     | awk -F'\t' '{n=$1; sub(".*/", "", n); print n "=" $2}' > "$ENV_FILE" || [ ! -s "$ENV_FILE" ]; then
  log 'Parameter Store 를 못 읽었습니다'
  exit 1
fi
# BE 와 같이 쓰는 값은 복사하지 않고 BE 경로에서 읽는다 (키를 바꿔도 한 곳만 고치면 되게)
SHARED_PATH=${SHARED_PATH:-/storix/prod/env/}
for pair in INTERNAL_API_KEY:STORIX_PROD_INTERNAL_API_KEY SLACK_BOT_TOKEN:SLACK_BOT_TOKEN SLACK_CHANNEL_ID:SLACK_CHANNEL_ID; do
  from=${pair%%:*} to=${pair##*:}
  grep -q "^$to=" "$ENV_FILE" && continue
  value=$(aws ssm get-parameter --name "$SHARED_PATH$from" --with-decryption --query Parameter.Value --output text 2>/dev/null) \
    && [ -n "$value" ] && echo "$to=$value" >> "$ENV_FILE" || log "$SHARED_PATH$from 를 못 읽었습니다"
done
# staging 계정은 기존 이름(STAGING_DB_USERNAME · STAGING_DB_PASSWORD, storix-stage 스킬도 씀)으로 있다. 크롤러가 읽는 이름으로도 둔다
for pair in STAGING_DB_USERNAME:MYSQL_DATABASE_USER STAGING_DB_PASSWORD:MYSQL_DATABASE_PASSWORD; do
  from=${pair%%:*} to=${pair##*:}
  grep -q "^$to=" "$ENV_FILE" && continue
  line=$(sed -n "s/^$from=/$to=/p" "$ENV_FILE")
  [ -n "$line" ] && echo "$line" >> "$ENV_FILE"
done

# 2) 코드 갱신
git() { command git -c safe.directory="$APP_DIR" "$@"; }  # root 로 돌아 소유자가 달라도 되게
before=$(git rev-parse HEAD)
if git fetch -q origin "$DEPLOY_REF" && git checkout -q --detach FETCH_HEAD; then
  after=$(git rev-parse HEAD)
  if [ "$before" != "$after" ] || ! docker image inspect storix-crawler:latest >/dev/null 2>&1; then
    log "이미지 빌드 ${before:0:7} → ${after:0:7}"
    docker compose build -q nightly || { slack "🚨 크롤러 이미지 빌드 실패 (${after:0:7})"; exit 1; }
  fi
else
  log "코드 갱신 실패 — 기존 코드로 진행"
fi

# 3) 실행
log '노션 작품 요청 처리 시작'
docker compose run --rm nightly
code=$?
log "끝 (종료 코드 $code)"
if [ "$code" -ne 0 ] && [ "$code" -ne 1 ]; then
  # 1 은 런 실패라 cli 가 이미 알렸다. 그 밖(컨테이너가 못 뜸 · 강제 종료 등)은 여기서 알린다
  slack "🚨 크롤러 야간 실행이 비정상 종료했습니다 (종료 코드 $code). journalctl -u storix-crawler-nightly 를 확인하세요."
fi
exit "$code"
