#!/usr/bin/env bash
# 크롤러 서버 야간 실행 (#77). 부팅 때 systemd(storix-crawler-nightly.service)가 한 번 부른다.
#   1) Parameter Store /storix/crawler/ → 환경변수 파일 (메모리 위 /run, 디스크에 안 남김)
#   2) main 을 받아 compose · 스크립트 갱신, 이미지는 ECR 에서 받음 (main 푸시 때 cd.yml 이 올림)
#   3) 로그인 쿠키: S3 에 더 새 것이 있으면 받음 (로컬에서 deploy/push_sessions.sh 로 올림)
#   4) 노션 작품 요청 처리 — 단, 슬랙 [다시 로그인] 버튼으로 켜졌으면(태그 crawler:mode=login) 폰 원격 로그인
#      (crawler/remote_login.py) 을 돌리고 성공하면 쿠키를 S3 에 올린다 (cli.py requests --env prod). 세션 만료 · 실패 알림은 cli 가 슬랙으로 보낸다
#   6) 서버 종료 — /etc/storix-crawler/hold 가 있으면 끄지 않는다 (들어가서 고칠 때)
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
ECR_IMAGE=${ECR_IMAGE:-697629627444.dkr.ecr.ap-northeast-2.amazonaws.com/storix-crawler:latest}
SESSIONS_BUCKET=${SESSIONS_BUCKET:-storix-crawler-sessions}

log() { echo "[nightly] $*"; }

# cli 까지 못 가고 죽으면 cli 가 알릴 수 없으니 여기서 알린다
slack() {  # slack <글> [채널] [스레드]
  local token channel
  token=$(grep -s '^SLACK_BOT_TOKEN=' "$ENV_FILE" | cut -d= -f2-)
  channel=${2:-$(grep -s '^SLACK_CHANNEL_ID=' "$ENV_FILE" | cut -d= -f2-)}
  [ -n "$token" ] && [ -n "$channel" ] || return 0
  curl -s -o /dev/null -X POST https://slack.com/api/chat.postMessage \
    -H "Authorization: Bearer $token" -H 'Content-Type: application/json; charset=utf-8' \
    --data "$(python3 -c 'import json,sys; print(json.dumps({"channel": sys.argv[1], "text": sys.argv[2], **({"thread_ts": sys.argv[3]} if sys.argv[3] else {})}))' "$channel" "$1" "${3:-}")"
}

# 스레드 답글과 같은 이모지로 원글에 반응한다. react <이모지 이름> <채널> <원글 ts>
react() {
  local token
  token=$(grep -s '^SLACK_BOT_TOKEN=' "$ENV_FILE" | cut -d= -f2-)
  [ -n "$token" ] && [ -n "$2" ] && [ -n "$3" ] || return 0
  curl -s -o /dev/null -X POST https://slack.com/api/reactions.add \
    -H "Authorization: Bearer $token" -H 'Content-Type: application/json; charset=utf-8' \
    --data "{\"channel\":\"$2\",\"timestamp\":\"$3\",\"name\":\"$1\"}"
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

# 2) 코드(compose · 스크립트) 갱신. 이미지는 서버에서 빌드하지 않고 ECR 에서 받는다
git() { command git -c safe.directory="$APP_DIR" "$@"; }  # root 로 돌아 소유자가 달라도 되게
before=$(git rev-parse HEAD)
if git fetch -q origin "$DEPLOY_REF" && git checkout -q --detach FETCH_HEAD; then
  log "코드 $(git rev-parse --short HEAD) ($DEPLOY_REF)"
else
  log "코드 갱신 실패 — 기존 코드로 진행"
fi
registry=${ECR_IMAGE%%/*}
if aws ecr get-login-password | docker login -u AWS --password-stdin "$registry" >/dev/null 2>&1 \
   && docker pull -q "$ECR_IMAGE" >/dev/null 2>&1; then
  docker tag "$ECR_IMAGE" storix-crawler:latest
  log "이미지 $(docker image inspect -f '{{index .RepoDigests 0}}' "$ECR_IMAGE" | cut -c1-90)"
elif docker image inspect storix-crawler:latest >/dev/null 2>&1 && [ "$before" = "$(git rev-parse HEAD)" ]; then
  log 'ECR 에서 못 받음 — 코드가 그대로라 기존 이미지로 진행'
else
  log 'ECR 에서 못 받음 — 코드가 바뀌었거나 이미지가 없어 직접 빌드'
  docker compose build -q nightly || { slack '🚨 크롤러 이미지를 받지도 빌드하지도 못했습니다'; exit 1; }
fi

# 3) 로그인 쿠키. S3 쪽이 서버 파일보다 새 것만 받는다 — 서버가 실행 중 다시 저장한 쿠키를 옛 것으로 덮지 않게
mkdir -p sessions
aws s3api list-objects-v2 --bucket "$SESSIONS_BUCKET" --prefix sessions/ \
    --query 'Contents[].[Key,LastModified]' --output text 2>/dev/null | while read -r key modified; do
  case "$key" in *.pkl) ;; *) continue ;; esac
  file="sessions/${key##*/}"
  remote=$(date -d "$modified" +%s) local_time=$(stat -c %Y "$file" 2>/dev/null || echo 0)
  if [ "$remote" -gt "$local_time" ] && aws s3 cp --only-show-errors "s3://$SESSIONS_BUCKET/$key" "$file"; then
    chmod 600 "$file"
    log "쿠키 갱신 ${key##*/}"
  fi
done

# 4) 무엇으로 켜졌나. 슬랙 [다시 로그인] 버튼이면 Lambda 가 태그를 달아 둔다. 다음 부팅이 또 로그인 모드가 되지 않게 바로 지운다
imds=$(curl -s -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')
instance_id=$(curl -s -H "X-aws-ec2-metadata-token: $imds" http://169.254.169.254/latest/meta-data/instance-id)
tag() { aws ec2 describe-tags --filters "Name=resource-id,Values=$instance_id" "Name=key,Values=$1" --query 'Tags[0].Value' --output text 2>/dev/null | grep -v '^None$'; }
mode=$(tag crawler:mode)
if [ "$mode" = login ]; then
  platform=$(tag crawler:login-platform) user=$(tag crawler:login-user)
  channel=$(tag crawler:login-channel) thread=$(tag crawler:login-thread)
  aws ec2 delete-tags --resources "$instance_id" --tags Key=crawler:mode Key=crawler:login-platform \
    Key=crawler:login-user Key=crawler:login-channel Key=crawler:login-thread
  log "원격 로그인 모드: $platform (요청 $user)"
  docker compose run --rm nightly python -m crawler.remote_login \
    --platform "$platform" --user "$user" --channel "$channel" ${thread:+--thread "$thread"}
  code=$?
  if [ "$code" -eq 0 ]; then
    case "$platform" in naver_*) cookie=naver ;; kakao_page) cookie=kakao ;; *) cookie=$platform ;; esac
    if aws s3 cp --only-show-errors --sse AES256 "sessions/${cookie}_cookies.pkl" "s3://$SESSIONS_BUCKET/sessions/${cookie}_cookies.pkl"; then
      slack ":white_check_mark: *$platform* 다시 로그인 완료 — 쿠키를 갱신했습니다. 다음 자정 실행부터 씁니다" "$channel" "$thread"
      react white_check_mark "$channel" "$thread"
    else
      slack ":warning: *$platform* 로그인은 됐지만 쿠키를 S3 에 못 올렸습니다. 서버 sessions/ 에는 있어 자정 실행은 됩니다" "$channel" "$thread"
      react warning "$channel" "$thread"
    fi
  fi
  exit "$code"
fi

# 5) 노션 작품 요청 처리. NIGHTLY_ARGS 로 명령을 바꿀 수 있다 (첫 확인: NIGHTLY_ARGS='python cli.py requests --env prod --dry-run')
log '노션 작품 요청 처리 시작'
# shellcheck disable=SC2086
docker compose run --rm nightly ${NIGHTLY_ARGS:-}
code=$?
log "끝 (종료 코드 $code)"
# 월요일(한국 시간)이면 지난주 새로 만든 작품을 슬랙으로. 없으면 보내지 않는다
if [ "$(TZ=Asia/Seoul date +%u)" = 1 ] && [ -z "${NIGHTLY_ARGS:-}" ]; then
  docker compose run --rm nightly python cli.py stage weekly --env prod --send || log '주간 작품 추가 알림 실패'
fi
if [ "$code" -ne 0 ] && [ "$code" -ne 1 ]; then
  # 1 은 런 실패라 cli 가 이미 알렸다. 그 밖(컨테이너가 못 뜸 · 강제 종료 등)은 여기서 알린다
  slack "🚨 크롤러 야간 실행이 비정상 종료했습니다 (종료 코드 $code). journalctl -u storix-crawler-nightly 를 확인하세요."
fi
exit "$code"
