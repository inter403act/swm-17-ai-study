## Backend

FastAPI backend for the Commentory GitHub webhook MVP.

### Setup

```bash
cd commentory/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set `GITHUB_TOKEN` in `.env` to a fine-grained PAT that can read pull requests and write issue comments for the target repository.

### Run

```bash
uvicorn main:app --reload
```

### Endpoints

- `GET /health`
- `POST /webhooks/github`

The webhook endpoint only handles `pull_request` events with `action: opened`. Signature verification, AI analysis, queues, and automated tests are intentionally omitted for this MVP.

### Webhook test quick start

`gh` CLI 로그인과 Node.js/npm이 필요하다. 테스트 스크립트는 `gh auth token`으로 `GITHUB_TOKEN`을 자동 설정한다.

```bash
cd commentory/backend
cp .env.example .env
WEBHOOK_FROM_REPO_URL=https://github.com/ai-tech-practice/temp-ai-tech-backend
./script/test_webhook_flow.sh "$WEBHOOK_FROM_REPO_URL"
```

`.env`에 `WEBHOOK_FROM_REPO_URL`을 저장했다면 `./script/test_webhook_flow.sh`만 실행해도 된다.
실행하면 `pull_request` webhook이 없을 경우 대상 repo에 자동 등록된다.
실행하면 로컬 FastAPI 서버와 smee relay가 자동으로 켜진다.
실행하면 테스트 branch를 push하고 새 PR을 생성한다.
GitHub가 `pull_request.opened` webhook을 smee를 통해 로컬 `/webhooks/github`로 보낸다.
백엔드는 PR 정보를 조회하고 `## Commentory MVP` 댓글을 작성한다.
성공하면 생성된 PR URL과 댓글 URL을 출력한다.
