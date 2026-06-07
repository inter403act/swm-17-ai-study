# swm-17-ai-study — Commentory

GitHub Pull Request가 열리면 webhook으로 이를 받아, AI agent가 변경 내용을 분석해 **요약 · 위험도 평가 · 리뷰 체크리스트**를 생성하고 PR에 리뷰 댓글로 남기는 데모.

단순히 diff만 보는 게 아니라, repository 컨텍스트(연관 파일·import·호출부)를 BM25 검색과 tree-sitter AST 분석으로 모아 LLM에 함께 제공해 리뷰 품질을 높인다.

```
PR open → webhook(smee) → backend → repo 컨텍스트 수집 → AI 워크플로우(LangGraph) → PR 댓글
                                                                 └ UI(Streamlit)가 진행 상황 실시간 표시
```

[샘플 코드 설명](sample/README.md)

## 기술 스택

| 영역 | 사용 기술 |
| --- | --- |
| 언어 | Python 3.13 |
| Backend | FastAPI, Uvicorn, httpx |
| AI 워크플로우 | LangGraph, LangChain |
| LLM | Solar (Upstage, `solar-pro2`) — `langchain-upstage` |
| 코드 분석 | tree-sitter (+ tree-sitter-java) AST, rank-bm25 검색 |
| UI | Streamlit |
| 연동 | GitHub Webhooks, smee.io (로컬 중계) |
| 인프라 | Docker Compose |

## 구성

| 디렉터리 | 역할 |
| --- | --- |
| `commentory/backend/` | FastAPI. webhook 수신, 컨텍스트 수집, 워크플로우 실행, PR 댓글 작성 |
| `commentory/ai/` | LangGraph 기반 AI agent — 요약 / 위험도 / 체크리스트 노드와 PR 분석 |
| `commentory/ui/` | Streamlit. backend를 polling 해 워크플로우 진행과 결과 표시 |
| `sample/` | 분석 대상 예시 코드 (Java) |

## 실행

Docker Desktop이 실행 중이어야 한다.

### 1. `.env` 작성

```bash
cd commentory
cp .env.example .env
```

`.env`에서 아래 2개를 채운다. 나머지는 기본값 그대로 둔다.

| 키 | 값 |
| --- | --- |
| `SOLAR_API_KEY` | Solar API Key |
| `GITHUB_TOKEN` | PR 읽기·댓글 권한이 있는 GitHub 토큰 (Classic, `repo` 스코프) |

`SMEE_URL`은 `.env.example`에 공유 채널(`https://smee.io/commentory-swm17-temp-ai-tech-backend`)이 기본값으로 들어 있어 그대로 두면 된다.

### 2. 실행

```bash
docker compose up -d --build
```

- UI: http://localhost:8501
- Backend: http://localhost:8000

종료는 `docker compose down`.

## 팀원 로컬 실행 (공유 채널)

모두 같은 smee 채널을 써서 각자 자기 로컬에서 분석 과정을 본다. 별도 webhook 등록이 필요 없다.

1. **Classic 토큰** 생성 (https://github.com/settings/tokens → Generate new token (classic) → `repo` 스코프) — 메인 레포에 `Write` 콜라보레이터여야 한다
2. `commentory/.env` 작성
   - `SOLAR_API_KEY` = 본인 키
   - `GITHUB_TOKEN` = 본인 토큰
   - `SMEE_URL` = `https://smee.io/commentory-swm17-temp-ai-tech-backend` (기본값 그대로)
3. `cd commentory && docker compose up -d --build`
4. 메인 레포에서 PR을 열면 각자 UI(http://localhost:8501)에 분석 과정이 표시된다

> smee 채널은 연결된 모든 로컬에 이벤트를 broadcast한다. 여러 명이 동시에 켜두면 각 백엔드가 PR에 댓글을 달아 **댓글이 중복**될 수 있다. 댓글을 하나만 남기려면 한 명만 백엔드를 켜둔다.
