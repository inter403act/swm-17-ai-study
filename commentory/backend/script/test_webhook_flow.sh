#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./script/test_webhook_flow.sh "$WEBHOOK_FROM_REPO_URL"

Example:
  WEBHOOK_FROM_REPO_URL=https://github.com/ai-tech-practice/temp-ai-tech-backend
  ./script/test_webhook_flow.sh "$WEBHOOK_FROM_REPO_URL"

Environment overrides:
  WEBHOOK_FROM_REPO_URL  Target GitHub repository URL.
  GITHUB_TOKEN           Optional. Defaults to `gh auth token`.
  SMEE_URL               Default: https://smee.io/commentory-swm17-temp-ai-tech-backend
  SERVER_PORT            Default: 8000

This script:
  1. Ensures a pull_request webhook exists for the smee URL.
  2. Starts the local FastAPI webhook server.
  3. Starts smee-client to forward GitHub webhooks to localhost.
  4. Creates a test PR.
  5. Waits until the Commentory MVP comment appears on the PR.
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
      echo "Repository URL must look like https://github.com/owner/repo." >&2
      exit 1
      ;;
  esac

  repo="${repo%.git}"
  repo="${repo%/}"

  if [[ ! "$repo" =~ ^[^/]+/[^/]+$ ]]; then
    echo "Repository URL must look like https://github.com/owner/repo." >&2
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

  echo "Timed out waiting for $label." >&2
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
    echo "Webhook already exists: $hook_id"
    return
  fi

  echo "Creating webhook for $TARGET_REPO..."
  gh api "repos/$TARGET_REPO/hooks" \
    -X POST \
    -f name=web \
    -F active=true \
    -f 'events[]=pull_request' \
    -f "config[url]=$SMEE_URL" \
    -f 'config[content_type]=json' \
    -f 'config[insecure_ssl]=0' \
    --jq '"Webhook created: \(.id)"'
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

  echo "Timed out waiting for Commentory comment on PR #$pr_number." >&2
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
  echo "Port $SERVER_PORT is already in use. Stop the existing process or set SERVER_PORT." >&2
  exit 1
fi

echo "Starting FastAPI server on 127.0.0.1:$SERVER_PORT..."
(
  cd "$BACKEND_DIR"
  uvicorn main:app --host 127.0.0.1 --port "$SERVER_PORT"
) > "$SERVER_LOG" 2>&1 &
SERVER_PID="$!"
wait_for_url "http://127.0.0.1:$SERVER_PORT/health" "FastAPI server"

echo "Starting smee-client..."
npx -y smee-client \
  --url "$SMEE_URL" \
  --target "http://127.0.0.1:$SERVER_PORT/webhooks/github" \
  > "$SMEE_LOG" 2>&1 &
SMEE_PID="$!"
sleep 2

echo "Creating test PR..."
PR_URL="$("$SCRIPT_DIR/create_test_pr.sh" "$TARGET_REPO" | tail -n 2 | head -n 1)"
PR_NUMBER="$(extract_pr_number "$PR_URL")"

echo "Waiting for Commentory comment on PR #$PR_NUMBER..."
COMMENT_URL="$(wait_for_comment "$PR_NUMBER")"

echo
echo "Webhook flow test succeeded."
echo "PR: $PR_URL"
echo "Comment: $COMMENT_URL"
