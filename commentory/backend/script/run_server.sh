#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
사용법:
  ./script/run_server.sh

환경변수:
  GITHUB_TOKEN   필수. GitHub PR 조회와 comment 작성 권한이 필요하다.
  SERVER_HOST    기본값: 0.0.0.0
  SERVER_PORT    기본값: PORT 또는 8000

이 스크립트는 FastAPI 백엔드 서버만 실행한다.
배포 환경에서는 GITHUB_TOKEN을 .env 파일이 아니라 배포 플랫폼의 환경변수로 설정하는 것을 권장한다.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

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

load_env

SERVER_HOST="${SERVER_HOST:-0.0.0.0}"
SERVER_PORT="${SERVER_PORT:-${PORT:-8000}}"

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "GITHUB_TOKEN이 필요합니다. .env 또는 배포 환경변수에 설정하세요." >&2
  exit 1
fi

cd "$BACKEND_DIR"
exec uvicorn main:app --host "$SERVER_HOST" --port "$SERVER_PORT"
