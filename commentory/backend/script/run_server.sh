#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
사용법:
  ./script/run_server.sh

환경변수:
  GITHUB_TOKEN       GitHub PR 조회와 comment 작성 권한이 필요하다. 비어 있으면 `gh auth token`을 사용한다.
  SMEE_URL           기본값: https://smee.io/commentory-swm17-temp-ai-tech-backend
  RUN_SMEE_CLIENT    기본값: true. 배포 환경에서는 false로 설정한다.
  SERVER_HOST        기본값: 0.0.0.0
  SERVER_PORT        기본값: PORT 또는 8000

이 스크립트는 FastAPI 백엔드 서버와 smee-client를 함께 실행한다.
배포 환경에서는 RUN_SMEE_CLIENT=false로 설정하고, GITHUB_TOKEN은 배포 플랫폼 환경변수로 설정하는 것을 권장한다.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$BACKEND_DIR/.." && pwd)"
SERVER_PID=""

load_env() {
  local env_file="$REPO_ROOT/.env"
  local line
  local key
  local value

  [[ -f "$env_file" ]] || return 0

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

load_env

SERVER_HOST="${SERVER_HOST:-0.0.0.0}"
SERVER_PORT="${SERVER_PORT:-${PORT:-8000}}"
SMEE_URL="${SMEE_URL:-https://smee.io/commentory-swm17-temp-ai-tech-backend}"
RUN_SMEE_CLIENT="${RUN_SMEE_CLIENT:-true}"

cleanup() {
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    export GITHUB_TOKEN="$(gh auth token)"
  else
    echo "GITHUB_TOKEN이 필요합니다. .env 또는 배포 환경변수에 설정하거나 gh CLI에 로그인하세요." >&2
    exit 1
  fi
fi

cd "$REPO_ROOT"

if [[ "$RUN_SMEE_CLIENT" == "false" ]]; then
  exec uvicorn commentory.backend.main:app --host "$SERVER_HOST" --port "$SERVER_PORT"
fi

if ! command -v npx >/dev/null 2>&1; then
  echo "smee-client 실행을 위해 Node.js/npm의 npx가 필요합니다." >&2
  exit 1
fi

echo "FastAPI 서버를 $SERVER_HOST:$SERVER_PORT 에서 실행합니다..."
uvicorn commentory.backend.main:app --host "$SERVER_HOST" --port "$SERVER_PORT" &
SERVER_PID="$!"

echo "smee-client를 실행합니다: $SMEE_URL -> http://127.0.0.1:$SERVER_PORT/webhooks/github"
npx -y smee-client \
  --url "$SMEE_URL" \
  --target "http://127.0.0.1:$SERVER_PORT/webhooks/github"
