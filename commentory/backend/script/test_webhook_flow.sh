#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
사용법:
  ./script/test_webhook_flow.sh "$WEBHOOK_FROM_REPO_URL"

예시:
  WEBHOOK_FROM_REPO_URL=https://github.com/ai-tech-practice/temp-ai-tech-backend
  ./script/test_webhook_flow.sh "$WEBHOOK_FROM_REPO_URL"

환경변수:
  WEBHOOK_FROM_REPO_URL  테스트 대상 GitHub repository URL.
  GITHUB_TOKEN           선택값. 기본값은 `gh auth token`.
  SMEE_URL               기본값: https://smee.io/commentory-swm17-temp-ai-tech-backend
  SERVER_PORT            기본값: 8000

이 스크립트가 하는 일:
  1. smee URL용 pull_request webhook이 있는지 확인한다.
  2. 로컬 FastAPI webhook 서버를 실행한다.
  3. GitHub webhook을 localhost로 전달하도록 smee-client를 실행한다.
  4. 테스트 PR을 생성한다.
  5. PR에 Commentory MVP 댓글이 생길 때까지 기다린다.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMMENT_MARKER="## Commentory MVP"
SERVER_PID=""
SMEE_PID=""
SERVER_LOG=""
SMEE_LOG=""

cleanup() {
  if [[ -n "$SMEE_PID" ]] && kill -0 "$SMEE_PID" >/dev/null 2>&1; then
    kill "$SMEE_PID" >/dev/null 2>&1 || true
  fi
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
  fi
  [[ -n "$SERVER_LOG" ]] && rm -f "$SERVER_LOG"
  [[ -n "$SMEE_LOG" ]] && rm -f "$SMEE_LOG"
}
trap cleanup EXIT

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "$1 is required." >&2
    exit 1
  fi
}

load_env() {
  local env_file="$BACKEND_DIR/.env"
  local line
  local key
  local value

  [[ -f "$env_file" ]] || return

  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" == \#* || "$line" != *=* ]] && continue

    key="${line%%=*}"
    value="${line#*=}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue

    if [[ -z "${!key:-}" ]]; then
      export "$key=$value"
    fi
  done < "$env_file"
}

repo_from_url() {
  local url="$1"
  local repo

  case "$url" in
    https://github.com/*)
      repo="${url#https://github.com/}"
      ;;
    git@github.com:*)
      repo="${url#git@github.com:}"
      ;;
    *)
      echo "Repository URL은 https://github.com/owner/repo 형식이어야 합니다." >&2
      exit 1
      ;;
  esac

  repo="${repo%.git}"
  repo="${repo%/}"

  if [[ ! "$repo" =~ ^[^/]+/[^/]+$ ]]; then
    echo "Repository URL은 https://github.com/owner/repo 형식이어야 합니다." >&2
    exit 1
  fi

  echo "$repo"
}

wait_for_url() {
  local url="$1"
  local label="$2"

  for _ in {1..30}; do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done

  echo "$label 대기 시간이 초과되었습니다." >&2
  exit 1
}

ensure_webhook() {
  local hook_id

  hook_id="$(
    gh api "repos/$TARGET_REPO/hooks" \
      --jq ".[] | select(.config.url == \"$SMEE_URL\") | select(.events | index(\"pull_request\")) | .id" \
      | head -n 1
  )"

  if [[ -n "$hook_id" ]]; then
    echo "Webhook이 이미 존재합니다: $hook_id"
    return
  fi

  echo "$TARGET_REPO repository에 webhook을 생성합니다..."
  gh api "repos/$TARGET_REPO/hooks" \
    -X POST \
    -f name=web \
    -F active=true \
    -f 'events[]=pull_request' \
    -f "config[url]=$SMEE_URL" \
    -f 'config[content_type]=json' \
    -f 'config[insecure_ssl]=0' \
    --jq '"Webhook 생성 완료: \(.id)"'
}

extract_pr_number() {
  local pr_url="$1"
  echo "${pr_url##*/}"
}

wait_for_comment() {
  local pr_number="$1"

  for _ in {1..30}; do
    local comment
    comment="$(
      gh api "repos/$TARGET_REPO/issues/$pr_number/comments" \
        --jq ".[] | select(.body | contains(\"$COMMENT_MARKER\")) | .html_url" \
        | head -n 1
    )"

    if [[ -n "$comment" ]]; then
      echo "$comment"
      return 0
    fi

    sleep 2
  done

  echo "PR #$pr_number 에 Commentory 댓글이 생성되기를 기다리다 시간이 초과되었습니다." >&2
  exit 1
}

require_command gh
require_command curl
require_command npx
require_command uvicorn

load_env

if [[ $# -gt 1 ]]; then
  usage
  exit 1
fi

REPO_URL="${1:-${WEBHOOK_FROM_REPO_URL:-}}"
if [[ -z "$REPO_URL" ]]; then
  usage
  exit 1
fi

SMEE_URL="${SMEE_URL:-https://smee.io/commentory-swm17-temp-ai-tech-backend}"
SERVER_PORT="${SERVER_PORT:-8000}"

gh auth status >/dev/null

TARGET_REPO="$(repo_from_url "$REPO_URL")"
export GITHUB_TOKEN="${GITHUB_TOKEN:-$(gh auth token)}"
SERVER_LOG="$(mktemp)"
SMEE_LOG="$(mktemp)"

ensure_webhook

if lsof -iTCP:"$SERVER_PORT" -sTCP:LISTEN -n -P >/dev/null 2>&1; then
  echo "$SERVER_PORT 포트가 이미 사용 중입니다. 기존 프로세스를 종료하거나 SERVER_PORT를 변경하세요." >&2
  exit 1
fi

echo "FastAPI 서버를 127.0.0.1:$SERVER_PORT 에서 실행합니다..."
(
  cd "$BACKEND_DIR"
  uvicorn main:app --host 127.0.0.1 --port "$SERVER_PORT"
) > "$SERVER_LOG" 2>&1 &
SERVER_PID="$!"
wait_for_url "http://127.0.0.1:$SERVER_PORT/health" "FastAPI server"

echo "smee-client를 실행합니다..."
npx -y smee-client \
  --url "$SMEE_URL" \
  --target "http://127.0.0.1:$SERVER_PORT/webhooks/github" \
  > "$SMEE_LOG" 2>&1 &
SMEE_PID="$!"
sleep 2

echo "테스트 PR을 생성합니다..."
PR_URL="$("$SCRIPT_DIR/create_test_pr.sh" "$TARGET_REPO" | tail -n 2 | head -n 1)"
PR_NUMBER="$(extract_pr_number "$PR_URL")"

echo "PR #$PR_NUMBER 에 Commentory 댓글이 생성되기를 기다립니다..."
COMMENT_URL="$(wait_for_comment "$PR_NUMBER")"

echo
echo "Webhook flow 테스트가 성공했습니다."
echo "PR: $PR_URL"
echo "Comment: $COMMENT_URL"
