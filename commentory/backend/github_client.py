import base64
from typing import Any

import httpx

from commentory.backend.config import GITHUB_API_URL, require_github_token


def _headers() -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {require_github_token()}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def get_pull_request(owner: str, repo: str, pull_number: int) -> dict[str, Any]:
    url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/pulls/{pull_number}"
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=_headers())
        response.raise_for_status()
        return response.json()


async def get_pull_request_files(owner: str, repo: str, pull_number: int) -> list[dict[str, Any]]:
    url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/pulls/{pull_number}/files"
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=_headers())
        response.raise_for_status()
        return response.json()


async def get_repository_tree(owner: str, repo: str, branch: str = "main") -> list[str]:
    async with httpx.AsyncClient() as client:
        branch_url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/branches/{branch}"
        branch_resp = await client.get(branch_url, headers=_headers())
        branch_resp.raise_for_status()
        tree_sha = branch_resp.json()["commit"]["commit"]["tree"]["sha"]

        tree_url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/git/trees/{tree_sha}?recursive=1"
        tree_resp = await client.get(tree_url, headers=_headers())
        tree_resp.raise_for_status()
        return [item["path"] for item in tree_resp.json().get("tree", []) if item["type"] == "blob"]


async def get_file_content(owner: str, repo: str, path: str, ref: str = "main") -> str | None:
    url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/contents/{path}?ref={ref}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=_headers())
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        if data.get("encoding") == "base64":
            return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return data.get("content")


async def create_pull_request(
    owner: str,
    repo: str,
    title: str,
    head: str,
    base: str,
    body: str,
) -> dict[str, Any]:
    url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/pulls"
    payload = {
        "title": title,
        "head": head,
        "base": base,
        "body": body,
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=_headers(), json=payload)
        response.raise_for_status()
        return response.json()


async def create_pr_comment(owner: str, repo: str, pull_number: int, body: str) -> dict[str, Any]:
    url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/issues/{pull_number}/comments"
    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=_headers(), json={"body": body})
        response.raise_for_status()
        return response.json()
