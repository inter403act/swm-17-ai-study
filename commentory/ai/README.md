## AI Agent

Commentory AI workflow는 GitHub PR 데이터를 입력받아 PR 영향 분석, 요약, 위험도 평가, 조건부 체크리스트 생성을 수행합니다.

### Workflow

```text
pr_analysis
-> summary, risk 병렬 실행
-> risk가 MEDIUM/HIGH이면 checklist 실행
-> risk가 LOW이면 checklist 생략
-> summary와 checklist/skip_checklist가 모두 끝나면 join
-> END
```

### Backend에서 사용할 메서드

#### 1. 동기 실행

외부 서비스에서 workflow 완료 결과만 필요하면 `run_workflow()`를 사용합니다.

```python
from graph import run_workflow

result_state = run_workflow(initial_state)
```

`run_workflow()`가 반환되면 agent workflow가 완료된 상태입니다.

#### 2. 진행 상태 스트리밍

Streamlit, polling, SSE, WebSocket 등에서 현재 실행 단계를 보여줘야 한다면 `stream_workflow_status()`를 사용합니다.

```python
from graph import stream_workflow_status

for status_event in stream_workflow_status(initial_state):
    print(status_event)
```

상태 이벤트 형식은 다음과 같습니다.

```python
{
    "status": "RUNNING",
    "current_step": "summary",
    "message": "PR 요약을 생성 중입니다.",
}
```

`status` 값은 다음 중 하나입니다.

- `PENDING`: workflow 실행 준비 중
- `RUNNING`: workflow 실행 중
- `COMPLETED`: workflow 정상 완료
- `FAILED`: workflow 실패

`current_step` 값은 다음 중 하나입니다.

- `pr_analysis`
- `summary`
- `risk`
- `checklist`
- `skip_checklist`
- `join`
- `completed`
- `failed`

백엔드에서 사용할 경우, FastAPI 기준으로 다음과 같이 사용 가능합니다. (Background Task + Job Store 방식)
```python
from fastapi import BackgroundTasks, FastAPI
from uuid import uuid4

from graph import run_workflow, stream_workflow_status

app = FastAPI()

JOB_STORE = {}


@app.post("/workflow/run")
def start_workflow(payload: dict, background_tasks: BackgroundTasks):
    job_id = str(uuid4())

    initial_state = {
        "pr_data": payload["pr_data"],
        "impact_context": None,
        "summary_result": None,
        "risk_result": None,
        "checklist_result": None,
        "comment_body": None,
    }

    JOB_STORE[job_id] = {
        "status": "PENDING",
        "current_step": None,
        "message": "Agent workflow 실행을 준비 중입니다.",
        "result": None,
        "error": None,
    }

    background_tasks.add_task(run_workflow_job, job_id, initial_state)

    return {"job_id": job_id}


def run_workflow_job(job_id: str, initial_state: dict):
    try:
        for event in stream_workflow_status(initial_state):
            JOB_STORE[job_id].update(event)

        result_state = run_workflow(initial_state)
        JOB_STORE[job_id]["result"] = result_state

    except Exception as error:
        JOB_STORE[job_id].update({
            "status": "FAILED",
            "current_step": "failed",
            "message": str(error),
            "error": str(error),
        })


@app.get("/workflow/{job_id}/status")
def get_workflow_status(job_id: str):
    return JOB_STORE[job_id]


@app.get("/workflow/{job_id}/result")
def get_workflow_result(job_id: str):
    return JOB_STORE[job_id]["result"]
```

### initial_state 형식

백엔드에서는 GitHub PR 조회 결과를 아래 형식으로 맞춰 workflow에 전달합니다.

```python
initial_state = {
    "pr_data": {
        "title": "PR title",
        "body": "PR description",
        "changed_files": [],
        "repo_tree": [],
        "repository_file_contents": [],
    },
    "impact_context": None,
    "summary_result": None,
    "risk_result": None,
    "checklist_result": None,
    "comment_body": None,
}
```

### 결과 데이터 사용 방식

workflow 결과 state에서 백엔드가 사용할 핵심 필드는 다음과 같습니다.

```python
summary_markdown = result_state["summary_result"]["markdown"]
risk_result = result_state["risk_result"]
checklist_result = result_state.get("checklist_result")
```

`summary_result["markdown"]`은 PR comment에 들어갈 요약 markdown입니다.

`risk_result`는 위험도 평가 결과입니다.

```python
risk_level = risk_result["risk_level"]
```

`risk_level`이 `LOW`이면 checklist agent를 실행하지 않으며, `checklist_result`는 `None`입니다.

`risk_level`이 `MEDIUM` 또는 `HIGH`이면 checklist agent를 실행하며, 결과는 다음 형식입니다.

```python
checklist_items = result_state["checklist_result"]["items"]
```

### PR comment 작성 책임

이 workflow는 GitHub PR comment를 직접 작성하지 않습니다.

백엔드 서비스는 workflow 완료 후 아래 데이터를 조합해 comment body를 만들고 GitHub API로 comment를 작성합니다.

- `summary_result["markdown"]`
- `risk_result`
- `checklist_result`

예시:

```python
parts = [result_state["summary_result"]["markdown"]]

checklist_result = result_state.get("checklist_result")
if checklist_result and checklist_result.get("items"):
    checklist_markdown = "\n".join(
        f"- {item}"
        for item in checklist_result["items"]
    )
    parts.append("### 리뷰 체크리스트\n" + checklist_markdown)

comment_body = "\n\n".join(parts)
```

### 테스트 및 결과
#### run_workflow() 결과 확인 테스트
```text
@'
import json
from graph import run_workflow

initial_state = {
    "pr_data": {
        "title": "Fix JWT expiration handling",
        "body": "JWT 만료 처리 방식을 수정합니다.",
        "changed_files": [
            {
                "filename": "auth/token.ts",
                "status": "modified",
                "additions": 12,
                "deletions": 8,
                "patch": """@@
- if (exp < now) throw ExpiredTokenError
+ if (exp <= now) throw ExpiredTokenError
function verifyToken(token) { return parseJwt(token); }
""",
            },
            {
                "filename": "auth/token.test.ts",
                "status": "modified",
                "additions": 5,
                "deletions": 1,
                "patch": """@@
+ it("rejects expired token boundary", () => {})
""",
            },
        ],
        "repo_tree": [
            "auth/token.ts",
            "auth/token.test.ts",
            "middleware/auth.ts",
        ],
        "repository_file_contents": [
            {
                "path": "middleware/auth.ts",
                "content": """import { verifyToken } from "../auth/token";
export function authMiddleware(req) {
    return verifyToken(req.token);
}
""",
            }
        ],
    },
    "impact_context": None,
    "summary_result": None,
    "risk_result": None,
    "checklist_result": None,
    "comment_body": None,
}

result = run_workflow(initial_state)

print("\n=== SUMMARY MARKDOWN ===")
print(result["summary_result"]["markdown"])

print("\n=== RISK RESULT ===")
print(json.dumps(result["risk_result"], ensure_ascii=False, indent=2))

print("\n=== CHECKLIST RESULT ===")
print(json.dumps(result.get("checklist_result"), ensure_ascii=False, indent=2))
'@ | python -
```
위 코드를 /commentory/ai 경로 터미널에서 실행하면 테스트가 가능합니다. 출력 결과는 아래와 같습니다.
실행 전에 아래 `pip install -r requirements.txt` 입력을 통해 의존성 설치가 필요 합니다.

```text
=== SUMMARY MARKDOWN ===
### 요약
- JWT 만료 처리 로직을 `exp < now`에서 `exp <= now`로 변경하여 경계 조건 처리 개선

### 주요 변경 사항
- `auth/token.ts`에서 토큰 만료 검증 조건 변경 (`<` → `<=`) 및 `verifyToken` 함수 추가
- `auth/token.test.ts`에 만료 토큰 경계 조건 테스트 케이스 추가

### 영향도 및 API 스코프
- 영향 도메인: authentication
- API: 없음
- 관련 파일: `middleware/auth.ts` (verifyToken 함수 사용)
- 관련 테스트: `auth/token.test.ts` (만료 토큰 경계 조건 테스트)

=== RISK RESULT ===
{
  "risk_level": "HIGH",
  "risk_reason": [
    "인증/보안 관련 로직 변경 (auth, jwt, token)",
    "핵심 비즈니스 로직 변경 (JWT 만료 처리 로직 수정)"
  ],
  "routing_target": "high_risk_checklist",
  "required_review_focus": [
    "JWT 만료 검증 로직 변경(exp < now → exp <= now)의 보안 영향 평가",
    "authMiddleware 등 관련 모듈에 대한 영향 분석",
    "테스트 커버리지 및 경계 조건 검증"
  ],
  "uncertainty": "medium"
}

=== CHECKLIST RESULT ===
{
  "items": [
    "JWT 만료 검증 로직 변경(exp < now → exp <= now)이 보안 취약점으로 이어질 수 있는지 확인",
    "authMiddleware에서 변경된 verifyToken 함수 호출 시 예외 처리 흐름이 적절한지 확인",
    "parseJwt, verifyToken 함수에서 토큰 파싱 및 검증 시 경계 조건 처리(예: exp == now) 테스트 케이스 추가 여부 확인",
    "인증/보안 변경에 대한 테스트 커버리지(특히 만료 토큰 경계 조건)가 충분한지 확인",
    "token.ts의 변경 사항이 다른 모듈(예: authMiddleware)에 미치는 영향 분석 여부 확인",
    "JWT 만료 검증 로직 변경이 기존 인증 흐름에 사이드이펙트를 발생시키지 않는지 확인"
  ]
}
```
위 테스트의 경우,

- `summary_result["markdown"]`
- `risk_result`
- `checklist_result`
- 
세 데이터를 나열한 출력 결과이므로, 백엔드에서 실제로 사용할 경우 별도의 처리가 필요합니다.

#### stream_workflow_status() 진행 상태 확인 테스트
```text
@'
import json
from graph import stream_workflow_status

initial_state = {
    "pr_data": {
        "title": "Fix JWT expiration handling",
        "body": "JWT 만료 처리 방식을 수정합니다.",
        "changed_files": [
            {
                "filename": "auth/token.ts",
                "status": "modified",
                "additions": 12,
                "deletions": 8,
                "patch": "- if (exp < now)\n+ if (exp <= now)",
            }
        ],
        "repo_tree": ["auth/token.ts", "middleware/auth.ts"],
        "repository_file_contents": [],
    },
    "impact_context": None,
    "summary_result": None,
    "risk_result": None,
    "checklist_result": None,
    "comment_body": None,
}

for event in stream_workflow_status(initial_state):
    print(json.dumps(event, ensure_ascii=False))
'@ | python -
```

위 코드를 /commentory/ai 경로 터미널에서 실행하면 테스트가 가능합니다. 출력 결과는 아래와 같습니다.
실행 전에 아래 `pip install -r requirements.txt` 입력을 통해 의존성 설치가 필요 합니다.

```text
{"status": "PENDING", "current_step": null, "message": "Agent workflow 실행을 준비 중입니다."}
{"status": "RUNNING", "current_step": "pr_analysis", "message": "PR 변경 내용과 영향 범위를 분석 중입니다."}
{"status": "RUNNING", "current_step": "risk", "message": "PR 위험도를 평가 중입니다."}
{"status": "RUNNING", "current_step": "summary", "message": "PR 요약을 생성 중입니다."}
{"status": "RUNNING", "current_step": "checklist", "message": "리뷰 체크리스트를 생성 중입니다."}
{"status": "RUNNING", "current_step": "join", "message": "워크플로우 결과를 정리 중입니다."}
{"status": "COMPLETED", "current_step": "completed", "message": "Agent workflow가 완료되었습니다."}
```