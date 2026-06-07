import asyncio
import base64
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Literal

import httpx

from commentory.ai.graph import run_workflow, stream_workflow_status

PR_URL_PATTERN = re.compile(
    r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<pull_number>\d+)/?$"
)
REPO_URL_PATTERN = re.compile(
    r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/?$"
)
DEFAULT_TEST_REPOSITORY_URL = "https://github.com/ai-tech-practice/temp-ai-tech-backend"
GITHUB_API_URL = os.getenv("GITHUB_API_URL", "https://api.github.com")
DEFAULT_BACKEND_API_URL = "http://backend:8000" if os.path.exists("/.dockerenv") else "http://localhost:8000"
BACKEND_API_URL = os.getenv("BACKEND_API_URL", DEFAULT_BACKEND_API_URL).rstrip("/")
MAX_REPOSITORY_CONTEXT_FILES = 20


@dataclass(frozen=True)
class PullRequestRef:
    owner: str
    repo: str
    pull_number: int

    @property
    def repository(self) -> str:
        return f"{self.owner}/{self.repo}"


@dataclass(frozen=True)
class RepositoryRef:
    owner: str
    repo: str

    @property
    def repository(self) -> str:
        return f"{self.owner}/{self.repo}"


@dataclass(frozen=True)
class IntegrationContracts:
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


def parse_repository_url(repository_url: str) -> RepositoryRef:
    match = REPO_URL_PATTERN.match(repository_url.strip())
    if match is None:
        raise ValueError("GitHub repository URL은 https://github.com/owner/repo 형식이어야 합니다.")

    return RepositoryRef(
        owner=match.group("owner"),
        repo=match.group("repo"),
    )


def load_contracts() -> IntegrationContracts:
    return IntegrationContracts(
        build_workflow_initial_state=build_workflow_initial_state_for_ui,
        build_comment_from_workflow_result=build_comment_from_workflow_result_for_ui,
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


def github_token() -> str:
    token = os.getenv("GITHUB_TOKEN")
    if token:
        return token

    try:
        completed = subprocess.run(
            ["gh", "auth", "token"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("GITHUB_TOKEN is not set and `gh auth token` failed") from exc

    token = completed.stdout.strip()
    if not token:
        raise RuntimeError("GITHUB_TOKEN is not set and `gh auth token` returned an empty token")
    return token


def github_headers() -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {github_token()}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def github_get(path: str, **params: Any) -> Any:
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{GITHUB_API_URL}{path}", headers=github_headers(), params=params)
        response.raise_for_status()
        return response.json()


async def github_post(path: str, payload: dict[str, Any]) -> Any:
    async with httpx.AsyncClient() as client:
        response = await client.post(f"{GITHUB_API_URL}{path}", headers=github_headers(), json=payload)
        response.raise_for_status()
        return response.json()


async def github_put(path: str, payload: dict[str, Any]) -> Any:
    async with httpx.AsyncClient() as client:
        response = await client.put(f"{GITHUB_API_URL}{path}", headers=github_headers(), json=payload)
        response.raise_for_status()
        return response.json()


async def backend_get(path: str, **params: Any) -> Any:
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(f"{BACKEND_API_URL}{path}", params=params)
        response.raise_for_status()
        return response.json()


def fetch_recent_workflow_runs() -> list[dict[str, Any]]:
    return run_async(backend_get("/workflow-runs/recent"))


def fetch_backend_workflow_run(repository: str, pull_number: int) -> dict[str, Any]:
    return run_async(
        backend_get(
            "/workflow-runs",
            repository=repository,
            pull_number=pull_number,
        )
    )


async def get_pull_request(owner: str, repo: str, pull_number: int) -> dict[str, Any]:
    return await github_get(f"/repos/{owner}/{repo}/pulls/{pull_number}")


async def get_pull_request_files(owner: str, repo: str, pull_number: int) -> list[dict[str, Any]]:
    return await github_get(f"/repos/{owner}/{repo}/pulls/{pull_number}/files")


async def get_pull_requests(owner: str, repo: str, state: str = "open") -> list[dict[str, Any]]:
    return await github_get(f"/repos/{owner}/{repo}/pulls", state=state)


async def get_repository_tree(owner: str, repo: str, branch: str = "main") -> list[str]:
    branch_data = await get_branch(owner, repo, branch)
    tree_sha = branch_data["commit"]["commit"]["tree"]["sha"]
    tree_data = await github_get(f"/repos/{owner}/{repo}/git/trees/{tree_sha}", recursive=1)
    return [
        item["path"]
        for item in tree_data.get("tree", [])
        if item.get("type") == "blob" and item.get("path")
    ]


async def get_file_content(owner: str, repo: str, path: str, ref: str = "main") -> str | None:
    try:
        data = await github_get(f"/repos/{owner}/{repo}/contents/{path}", ref=ref)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None
        raise

    if isinstance(data, list):
        return None

    if data.get("encoding") == "base64":
        return base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
    return data.get("content")


async def get_repository(owner: str, repo: str) -> dict[str, Any]:
    return await github_get(f"/repos/{owner}/{repo}")


async def get_branch(owner: str, repo: str, branch: str) -> dict[str, Any]:
    return await github_get(f"/repos/{owner}/{repo}/branches/{branch}")


async def create_git_ref(owner: str, repo: str, branch: str, sha: str) -> dict[str, Any]:
    return await github_post(
        f"/repos/{owner}/{repo}/git/refs",
        {"ref": f"refs/heads/{branch}", "sha": sha},
    )


async def create_file(
    owner: str,
    repo: str,
    path: str,
    message: str,
    content: str,
    branch: str,
) -> dict[str, Any]:
    encoded_content = base64.b64encode(content.encode("utf-8")).decode("ascii")
    return await github_put(
        f"/repos/{owner}/{repo}/contents/{path}",
        {"message": message, "content": encoded_content, "branch": branch},
    )


async def create_pull_request(
    owner: str,
    repo: str,
    title: str,
    head: str,
    base: str,
    body: str,
) -> dict[str, Any]:
    return await github_post(
        f"/repos/{owner}/{repo}/pulls",
        {"title": title, "head": head, "base": base, "body": body},
    )


async def create_pr_comment(owner: str, repo: str, pull_number: int, body: str) -> dict[str, Any]:
    return await github_post(f"/repos/{owner}/{repo}/issues/{pull_number}/comments", {"body": body})


def build_workflow_initial_state_for_ui(
    pull_request: dict[str, Any],
    changed_files: list[dict[str, Any]] | None = None,
    repo_tree: list[str] | None = None,
    repository_file_contents: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "pr_data": {
            "title": pull_request.get("title", ""),
            "body": pull_request.get("body", ""),
            "changed_files": changed_files or [],
            "repo_tree": repo_tree or [],
            "repository_file_contents": repository_file_contents or [],
        },
        "impact_context": None,
        "summary_result": None,
        "risk_result": None,
        "checklist_result": None,
        "comment_body": None,
    }


def build_comment_from_workflow_result_for_ui(
    repository: str,
    pull_number: int,
    workflow_result: dict[str, Any],
) -> str:
    parts = [
        "## Commentory",
        "",
        f"- Repository: `{repository}`",
        f"- PR: `#{pull_number}`",
    ]

    summary_markdown = (workflow_result.get("summary_result") or {}).get("markdown")
    if summary_markdown:
        parts.extend(["", summary_markdown])

    risk_result = workflow_result.get("risk_result") or {}
    risk_level = risk_result.get("risk_level")
    if risk_level:
        parts.extend(["", "### 위험도", f"- Level: `{risk_level}`"])

    risk_reasons = risk_result.get("risk_reason") or []
    if risk_reasons:
        parts.append("- Reason:")
        parts.extend(f"  - {reason}" for reason in risk_reasons)

    checklist_items = (workflow_result.get("checklist_result") or {}).get("items") or []
    if checklist_items:
        parts.extend(["", "### 리뷰 체크리스트"])
        parts.extend(f"- {item}" for item in checklist_items)

    return "\n".join(parts)


def fetch_workflow_input(pr_ref: PullRequestRef) -> dict[str, Any]:
    contracts = load_contracts()
    pull_request = run_async(get_pull_request(pr_ref.owner, pr_ref.repo, pr_ref.pull_number))
    changed_files = run_async(get_pull_request_files(pr_ref.owner, pr_ref.repo, pr_ref.pull_number))
    repo_tree, repository_file_contents = run_async(
        build_repository_context(pr_ref.owner, pr_ref.repo, pull_request, changed_files)
    )

    return {
        "pull_request": pull_request,
        "changed_files": changed_files,
        "repo_tree": repo_tree,
        "repository_file_contents": repository_file_contents,
        "initial_state": contracts.build_workflow_initial_state(
            pull_request,
            changed_files,
            repo_tree,
            repository_file_contents,
        ),
    }


async def build_repository_context(
    owner: str,
    repo: str,
    pull_request: dict[str, Any],
    changed_files: list[dict[str, Any]],
) -> tuple[list[str], list[dict[str, str]]]:
    base_ref = pull_request.get("base", {}).get("ref") or "main"
    repo_tree = await get_repository_tree(owner, repo, branch=base_ref)

    changed_paths = {
        file_data["filename"]
        for file_data in changed_files
        if file_data.get("filename")
    }
    related_paths = select_repository_context_paths(repo_tree, changed_paths)

    repository_file_contents: list[dict[str, str]] = []
    for path in related_paths:
        content = await get_file_content(owner, repo, path, ref=base_ref)
        if content is not None:
            repository_file_contents.append({"path": path, "content": content})

    return repo_tree, repository_file_contents


def select_repository_context_paths(repo_tree: list[str], changed_paths: set[str]) -> list[str]:
    changed_dirs = {
        path.rsplit("/", 1)[0]
        for path in changed_paths
        if "/" in path
    }

    if not changed_dirs:
        return [
            path
            for path in repo_tree
            if path not in changed_paths
        ][:MAX_REPOSITORY_CONTEXT_FILES]

    return [
        path
        for path in repo_tree
        if path not in changed_paths and any(path.startswith(directory + "/") for directory in changed_dirs)
    ][:MAX_REPOSITORY_CONTEXT_FILES]


def fetch_open_pull_requests(repository_ref: RepositoryRef) -> list[dict[str, Any]]:
    pull_requests = run_async(get_pull_requests(repository_ref.owner, repository_ref.repo, state="open"))
    return [
        {
            "number": pull_request.get("number"),
            "title": pull_request.get("title") or "Untitled PR",
            "state": pull_request.get("state"),
            "user": (pull_request.get("user") or {}).get("login"),
            "html_url": pull_request.get("html_url"),
            "head": (pull_request.get("head") or {}).get("ref"),
            "base": (pull_request.get("base") or {}).get("ref"),
        }
        for pull_request in pull_requests
    ]


RiskLevel = Literal["HIGH", "MEDIUM", "LOW"]


RISK_PR_TEMPLATES: dict[RiskLevel, dict[str, str]] = {
    "HIGH": {
        "title": "commentory-risk-high: change auth token permission logic",
        "body": (
            "Commentory HIGH risk test PR.\n\n"
            "This PR intentionally adds authentication, JWT token, password, and permission logic "
            "so the workflow can exercise the HIGH risk route."
        ),
        "path_prefix": "src/main/java/commentory/risk/high",
        "filename": "AuthTokenPermissionProbe.java",
        "content": """package commentory.risk.high;

import java.time.Instant;
import java.util.Set;

public class AuthTokenPermissionProbe {
    private final Set<String> adminTokens = Set.of("root-token");

    public boolean validateJwtTokenAndPermission(String jwtToken, String password, String permission) {
        if (jwtToken == null || jwtToken.isBlank()) {
            return false;
        }
        if (password == null || password.length() < 8) {
            return false;
        }
        if ("DELETE_USER".equals(permission) && !adminTokens.contains(jwtToken)) {
            return false;
        }
        return Instant.now().getEpochSecond() > 0;
    }
}
""",
    },
    "MEDIUM": {
        "title": "commentory-risk-medium: add task service endpoint logic",
        "body": (
            "Commentory MEDIUM risk test PR.\n\n"
            "This PR adds service/API style runtime behavior without auth, payment, or schema changes "
            "so the workflow can exercise the MEDIUM risk route."
        ),
        "path_prefix": "src/main/java/commentory/risk/medium",
        "filename": "TaskLimitServiceProbe.java",
        "content": """package commentory.risk.medium;

import java.util.List;

public class TaskLimitServiceProbe {
    public List<String> listRecentTasks(List<String> tasks, int requestedLimit) {
        int limit = requestedLimit <= 0 ? 20 : Math.min(requestedLimit, 50);
        return tasks.stream()
            .limit(limit)
            .toList();
    }
}
""",
    },
    "LOW": {
        "title": "commentory-risk-low: update review guide document",
        "body": (
            "Commentory LOW risk test PR.\n\n"
            "This PR only adds documentation so the workflow can exercise the LOW risk route."
        ),
        "path_prefix": "docs/commentory-risk",
        "filename": "low-risk-review-guide.md",
        "content": """# Commentory LOW Risk Review Guide

This document is a low-risk PR fixture.

- It changes documentation only.
- It does not change runtime behavior.
- It does not modify auth, API, payment, database, or service logic.
""",
    },
}


def create_risk_test_pull_request(repository_ref: RepositoryRef, risk_level: RiskLevel) -> PullRequestRef:
    template = RISK_PR_TEMPLATES[risk_level]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    branch_name = f"commentory-risk-{risk_level.lower()}-{timestamp}"
    file_path = f"{template['path_prefix']}/{timestamp}-{template['filename']}"

    repository = run_async(get_repository(repository_ref.owner, repository_ref.repo))
    base_branch = repository.get("default_branch") or "main"
    base_branch_data = run_async(get_branch(repository_ref.owner, repository_ref.repo, base_branch))
    base_sha = base_branch_data["commit"]["sha"]

    run_async(create_git_ref(repository_ref.owner, repository_ref.repo, branch_name, base_sha))
    run_async(
        create_file(
            repository_ref.owner,
            repository_ref.repo,
            file_path,
            f"Create Commentory {risk_level} risk fixture",
            template["content"],
            branch_name,
        )
    )
    pull_request = run_async(
        create_pull_request(
            repository_ref.owner,
            repository_ref.repo,
            template["title"],
            branch_name,
            base_branch,
            template["body"],
        )
    )

    return PullRequestRef(
        owner=repository_ref.owner,
        repo=repository_ref.repo,
        pull_number=int(pull_request["number"]),
    )


def run_agent_workflow_for_ui(initial_state: dict[str, Any]) -> Iterator[dict[str, Any]]:
    contracts = load_contracts()

    for event in contracts.stream_workflow_status(initial_state):
        result = event.get("result")
        yield {
            "type": "result" if result is not None else "status",
            "event": event,
            "result": result,
        }


def build_comment_body(repository: str, pull_number: int, workflow_result: dict[str, Any]) -> str:
    contracts = load_contracts()
    return contracts.build_comment_from_workflow_result(repository, pull_number, workflow_result)


def post_comment(pr_ref: PullRequestRef, body: str) -> dict[str, Any]:
    return run_async(create_pr_comment(pr_ref.owner, pr_ref.repo, pr_ref.pull_number, body))
