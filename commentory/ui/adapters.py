import asyncio
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator


REPO_ROOT = Path(__file__).resolve().parents[2]
COMMENTORY_DIR = REPO_ROOT / "commentory"
for import_root in (REPO_ROOT, COMMENTORY_DIR / "backend", COMMENTORY_DIR / "ai"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))


PR_URL_PATTERN = re.compile(
    r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<pull_number>\d+)/?$"
)


class IntegrationDependencyError(RuntimeError):
    pass


@dataclass(frozen=True)
class PullRequestRef:
    owner: str
    repo: str
    pull_number: int

    @property
    def repository(self) -> str:
        return f"{self.owner}/{self.repo}"


@dataclass(frozen=True)
class IntegrationContracts:
    get_pull_request: Callable[..., Any]
    get_pull_request_files: Callable[..., Any]
    create_pr_comment: Callable[..., Any]
    build_workflow_initial_state: Callable[..., dict[str, Any]]
    build_comment_from_workflow_result: Callable[..., str]
    stream_workflow_status: Callable[..., Iterator[dict[str, Any]]]
    run_workflow: Callable[..., dict[str, Any]]


def parse_pr_url(pr_url: str) -> PullRequestRef:
    match = PR_URL_PATTERN.match(pr_url.strip())
    if match is None:
        raise ValueError("GitHub PR URL은 https://github.com/owner/repo/pull/number 형식이어야 합니다.")

    return PullRequestRef(
        owner=match.group("owner"),
        repo=match.group("repo"),
        pull_number=int(match.group("pull_number")),
    )


def load_contracts() -> IntegrationContracts:
    try:
        from commentory.ai.graph import run_workflow, stream_workflow_status
        from commentory.backend.github_client import (
            create_pr_comment,
            get_pull_request,
            get_pull_request_files,
        )
        from commentory.backend.main import (
            build_comment_from_workflow_result,
            build_workflow_initial_state,
        )
    except (ImportError, ModuleNotFoundError) as exc:
        raise IntegrationDependencyError(
            "PR #7 agent workflow, PR #8 backend helper, ai/backend Python dependencies가 "
            "함께 준비된 worktree에서 실행해야 합니다."
        ) from exc

    return IntegrationContracts(
        get_pull_request=get_pull_request,
        get_pull_request_files=get_pull_request_files,
        create_pr_comment=create_pr_comment,
        build_workflow_initial_state=build_workflow_initial_state,
        build_comment_from_workflow_result=build_comment_from_workflow_result,
        stream_workflow_status=stream_workflow_status,
        run_workflow=run_workflow,
    )


def run_async(awaitable: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(awaitable)
    finally:
        loop.close()


def fetch_workflow_input(pr_ref: PullRequestRef) -> dict[str, Any]:
    contracts = load_contracts()
    pull_request = run_async(contracts.get_pull_request(pr_ref.owner, pr_ref.repo, pr_ref.pull_number))
    changed_files = run_async(
        contracts.get_pull_request_files(pr_ref.owner, pr_ref.repo, pr_ref.pull_number)
    )

    return {
        "pull_request": pull_request,
        "changed_files": changed_files,
        "initial_state": contracts.build_workflow_initial_state(pull_request, changed_files),
    }


def run_agent_workflow_for_ui(initial_state: dict[str, Any]) -> Iterator[dict[str, Any]]:
    contracts = load_contracts()
    workflow_result = None

    for event in contracts.stream_workflow_status(initial_state):
        workflow_result = event.get("result") or workflow_result
        yield {
            "type": "status",
            "event": event,
            "result": None,
        }

    if workflow_result is None:
        # Current PR #7 streams progress only. Keep this fallback isolated so the
        # UI can switch to a result-bearing stream without changing screen code.
        workflow_result = contracts.run_workflow(initial_state)

    yield {
        "type": "result",
        "event": {
            "status": "COMPLETED",
            "current_step": "completed",
            "message": "Agent workflow 결과를 불러왔습니다.",
        },
        "result": workflow_result,
    }


def build_comment_body(repository: str, pull_number: int, workflow_result: dict[str, Any]) -> str:
    contracts = load_contracts()
    return contracts.build_comment_from_workflow_result(repository, pull_number, workflow_result)


def post_comment(pr_ref: PullRequestRef, body: str) -> dict[str, Any]:
    contracts = load_contracts()
    return run_async(contracts.create_pr_comment(pr_ref.owner, pr_ref.repo, pr_ref.pull_number, body))
