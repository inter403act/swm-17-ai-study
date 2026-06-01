from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request

from github_client import create_pr_comment, get_pull_request


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
        comment = build_comment(repo_info["full_name"], pull_number, pull_request.get("title", ""))
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
