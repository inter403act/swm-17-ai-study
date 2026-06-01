from typing import Any

import httpx

from config import GITHUB_API_URL, require_github_token


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
