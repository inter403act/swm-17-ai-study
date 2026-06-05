## Commentory UI

Streamlit 기반 workflow 확인 콘솔이다. 실제 GitHub PR을 만들거나 불러와 agent workflow 진행 상태를 확인한다.

### 실행 전제

- `SOLAR_API_KEY`와 `GITHUB_TOKEN`은 실행 환경 또는 각 모듈의 `.env` 규칙에 맞춰 설정한다.
- 통합 workflow 실행은 Python 3.13에서 검증했다. Python 3.14에서는 `commentory/ai/requirements.txt`의 `tokenizers==0.20.3` 빌드가 실패할 수 있다.

### 실행

```bash
pip install -r commentory/ai/requirements.txt
pip install -r commentory/ui/requirements.txt
streamlit run commentory/ui/app.py
```

앱 실행 후 기본 repository인 `https://github.com/ai-tech-practice/temp-ai-tech-backend`에서 `HIGH`, `MEDIUM`, `LOW` 버튼을 누르면 해당 위험도 fixture용 실제 PR을 생성하고 workflow를 바로 실행한다.

기존 PR을 사용하려면 `Load Open PRs`로 열린 PR 목록을 불러온 뒤 PR을 선택하고 `Fetch Selected PR`, `Run Workflow` 순서로 실행하면 된다. `GITHUB_TOKEN`이 비어 있으면 로컬 `gh auth token`을 fallback으로 사용한다.

PR을 불러올 때 UI는 backend webhook과 동일하게 base branch 기준 `repo_tree`와 관련 `repository_file_contents`를 함께 구성해 agent workflow에 전달한다.
