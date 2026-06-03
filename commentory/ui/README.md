## Commentory UI

Streamlit 기반 workflow 확인 콘솔이다. 이 브랜치는 UI만 독립적으로 추가하며, PR #7 agent workflow와 PR #8 backend helper 코드는 포함하지 않는다.

### 실행 전제

- PR #7의 `commentory.ai.graph.stream_workflow_status()`와 `run_workflow()`가 필요하다.
- PR #8의 `commentory.backend.github_client`와 workflow comment helper가 필요하다.
- `SOLAR_API_KEY`와 `GITHUB_TOKEN`은 실행 환경 또는 각 모듈의 `.env` 규칙에 맞춰 설정한다.
- 통합 workflow 실행은 Python 3.13에서 검증했다. Python 3.14에서는 `commentory/ai/requirements.txt`의 `tokenizers==0.20.3` 빌드가 실패할 수 있다.

### 실행

```bash
cd commentory/ui
pip install -r requirements.txt
streamlit run app.py
```

### 통합 테스트용 worktree 예시

```bash
git worktree add ../swm-17-ai-study-ui-integration-test develop
cd ../swm-17-ai-study-ui-integration-test
git checkout -b test/commentory-ui-integration
git merge origin/feature/agentic-workflow
git merge origin/commentory-fastapi-webhook-mvp
git merge origin/feature/commentory-streamlit-ui
python3.13 -m venv .venv-ui
source .venv-ui/bin/activate
pip install -r commentory/ai/requirements.txt
pip install -r commentory/backend/requirements.txt
pip install -r commentory/ui/requirements.txt
streamlit run commentory/ui/app.py
```

현재 PR #7의 `stream_workflow_status()`는 최종 workflow result를 이벤트에 포함하지 않는다. UI는 `commentory/ui/adapters.py`에서 이 차이를 감싸며, 나중에 result 포함 streaming API가 생기면 어댑터 내부만 교체하면 된다.
