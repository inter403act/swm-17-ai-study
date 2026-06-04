from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request

from github_client import (
    create_pr_comment,
    get_file_content,
    get_pull_request,
    get_pull_request_files,
    get_repository_tree,
)

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../ai")))
from graph import run_workflow


app = FastAPI(title="Commentory Backend")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/webhooks/github")
async def github_webhook(
    request: Request,
    x_github_event: str | None = Header(default=None),
) -> dict[str, Any]:
    if x_github_event != "pull_request":
        return {"status": "ignored", "reason": "unsupported_event"}

    payload = await request.json()
    if payload.get("action") != "opened":
        return {"status": "ignored", "reason": "unsupported_action"}

    try:
        repo_info = payload["repository"]
        owner = repo_info["owner"]["login"]
        repo = repo_info["name"]
        pull_number = int(payload["pull_request"]["number"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid pull_request payload") from exc

    try:
        pull_request = await get_pull_request(owner, repo, pull_number)
        changed_files = await get_pull_request_files(owner, repo, pull_number)

        base_branch = pull_request.get("base", {}).get("ref", "main")
        repo_tree = await get_repository_tree(owner, repo, branch=base_branch)

        # diff(patch)만으로는 변경 메서드 전체를 볼 수 없어 dataflow(죽은 인자 등) 분석이
        # 불가능하므로, 변경 파일의 head ref 전체 본문을 함께 실어준다. (삭제 파일은 본문 없음)
        head_ref = pull_request.get("head", {}).get("sha") or pull_request.get("head", {}).get("ref")
        if head_ref:
            for changed_file in changed_files:
                if changed_file.get("status") == "removed":
                    continue
                head_content = await get_file_content(owner, repo, changed_file["filename"], ref=head_ref)
                if head_content is not None:
                    changed_file["content"] = head_content

        changed_paths = {f["filename"] for f in changed_files}
        changed_dirs = {f["filename"].rsplit("/", 1)[0] for f in changed_files if "/" in f["filename"]}
        candidate_paths = [
            p for p in repo_tree
            if any(p.startswith(d + "/") for d in changed_dirs) and p not in changed_paths
        ][:20]

        repository_file_contents = []
        for path in candidate_paths:
            content = await get_file_content(owner, repo, path, ref=base_branch)
            if content:
                repository_file_contents.append({"path": path, "content": content})

        # Enable this block after the AI workflow branch is merged.
        initial_state = build_workflow_initial_state(pull_request, changed_files, repo_tree, repository_file_contents)
        workflow_result = run_workflow(initial_state)
        comment = build_comment_from_workflow_result(repo_info["full_name"], pull_number, workflow_result)

        #comment = build_comment(repo_info["full_name"], pull_number, pull_request.get("title", ""))
        created_comment = await create_pr_comment(owner, repo, pull_number, comment)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "GitHub API request failed",
                "status_code": exc.response.status_code,
                "response": exc.response.text,
            },
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "status": "comment_created",
        "repository": repo_info["full_name"],
        "pull_number": pull_number,
        "comment_url": created_comment.get("html_url"),
    }


def build_comment(repository: str, pull_number: int, title: str) -> str:
    return "\n".join(
        [
            "## Commentory MVP",
            "",
            f"- Repository: `{repository}`",
            f"- PR: `#{pull_number}`",
            f"- Title: {title}",
            "",
            "GitHub webhook and REST API integration succeeded.",
        ]
    )


def build_workflow_initial_state(
    pull_request: dict[str, Any],
    changed_files: list[dict[str, Any]] | None = None,
    repo_tree: list[str] | None = None,
    repository_file_contents: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the state contract expected by commentory.ai.graph.run_workflow()."""
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


def build_comment_from_workflow_result(
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
