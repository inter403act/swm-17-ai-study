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
