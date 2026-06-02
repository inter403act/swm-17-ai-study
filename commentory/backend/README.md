## 백엔드

Commentory GitHub webhook MVP를 위한 FastAPI 백엔드.

### 설정

```bash
cd commentory/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

직접 실행하거나 배포할 때는 `.env` 또는 배포 환경변수에 `GITHUB_TOKEN`을 설정한다. 대상 repo의 pull request를 읽고 issue comment를 작성할 수 있는 fine-grained PAT가 필요하다.

### 실행

```bash
./script/run_server.sh
```

로컬에서는 FastAPI 서버와 smee-client가 함께 실행된다. 실제 배포 환경에서는 `RUN_SMEE_CLIENT=false`로 설정하고 GitHub webhook payload URL을 배포 서버의 `/webhooks/github`로 지정한다.

### 엔드포인트

- `GET /health`
- `POST /webhooks/github`

webhook 엔드포인트는 `pull_request` 이벤트 중 `action: opened`만 처리한다. 이 MVP에서는 서명 검증, AI 분석, queue, 자동화 테스트를 의도적으로 생략했다.

### Webhook 테스트 Quick start

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
