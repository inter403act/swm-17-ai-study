# swm-17-ai-study

GitHub PR webhook을 받아 AI가 PR을 분석하고 리뷰 댓글을 작성하는 데모.

[샘플 코드 설명](sample/README.md)

## 실행

Docker Desktop이 실행 중이어야 한다.

### 1. `.env` 작성

```bash
cd commentory
cp .env.example .env
```

`.env`에서 아래 3개를 채운다. 나머지는 기본값 그대로 둔다.

| 키 | 값 |
| --- | --- |
| `SOLAR_API_KEY` | Solar API Key |
| `GITHUB_TOKEN` | PR 읽기·댓글 권한이 있는 GitHub 토큰 |
| `SMEE_URL` | repo webhook에 등록된 smee 채널 URL과 동일한 값 |

### 2. 실행

```bash
docker compose up -d --build
```

- UI: http://localhost:8501
- Backend: http://localhost:8000

종료는 `docker compose down`.
