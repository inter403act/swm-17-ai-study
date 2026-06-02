#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./script/create_test_pr.sh owner/repo [base-branch]

Environment overrides:
  PR_BRANCH_PREFIX  Branch prefix. Default: commentory-webhook-test
  PR_TITLE          Pull request title. Default: Test Commentory webhook
  PR_BODY           Pull request body.

Example:
  ./script/create_test_pr.sh ai-tech-practice/temp-ai-tech-backend
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

TARGET_REPO="${1:-}"
if [[ -z "$TARGET_REPO" ]]; then
  usage
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI is required." >&2
  exit 1
fi

gh auth status >/dev/null

BASE_BRANCH="${2:-$(gh api "repos/$TARGET_REPO" --jq '.default_branch')}"
BRANCH_PREFIX="${PR_BRANCH_PREFIX:-commentory-webhook-test}"
TIMESTAMP="$(date +%Y%m%d%H%M%S)"
BRANCH_NAME="${BRANCH_PREFIX}-${TIMESTAMP}"
PR_TITLE="${PR_TITLE:-Test Commentory webhook}"
PR_BODY="${PR_BODY:-This PR verifies that Commentory receives pull_request.opened and creates the preset PR comment.}"

WORKDIR="$(mktemp -d)"
cleanup() {
  rm -rf "$WORKDIR"
}
trap cleanup EXIT

echo "Cloning $TARGET_REPO into a temporary directory..."
gh repo clone "$TARGET_REPO" "$WORKDIR/repo" -- --quiet
cd "$WORKDIR/repo"

git checkout "$BASE_BRANCH" >/dev/null 2>&1
git checkout -b "$BRANCH_NAME" >/dev/null

TEST_FILE="commentory-webhook-test-${TIMESTAMP}.md"
cat > "$TEST_FILE" <<EOF
# Commentory webhook test

- Repository: $TARGET_REPO
- Branch: $BRANCH_NAME
- Created at: $(date -u +"%Y-%m-%dT%H:%M:%SZ")

This file was generated to verify the Commentory pull request webhook flow.
EOF

git add "$TEST_FILE"
git commit -m "Test Commentory webhook" >/dev/null
git push -u origin "$BRANCH_NAME" >/dev/null

echo "Creating pull request..."
PR_URL="$(
  gh pr create \
    --repo "$TARGET_REPO" \
    --base "$BASE_BRANCH" \
    --head "$BRANCH_NAME" \
    --title "$PR_TITLE" \
    --body "$PR_BODY"
)"

echo "$PR_URL"
echo "Wait for the Commentory MVP comment on the PR."
