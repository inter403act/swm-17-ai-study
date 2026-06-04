"""JWT token validation and permission check utilities for PR analysis pipeline."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any


SECRET_KEY = "commentory-jwt-secret"
ALGORITHM = "HS256"
TOKEN_EXPIRY_SECONDS = 3600


def validate_jwt_token(token: str) -> dict[str, Any] | None:
    """Validate JWT and return decoded payload, or None if invalid."""
    try:
        header, payload, signature = token.split(".")
        expected_sig = _sign(f"{header}.{payload}")
        if not hmac.compare_digest(signature, expected_sig):
            return None
        import base64, json
        decoded = json.loads(base64.urlsafe_b64decode(payload + "=="))
        return decoded
    except Exception:
        return None


def check_permission(user_id: str, resource: str, action: str) -> bool:
    """Return True if user has permission to perform action on resource."""
    acl: dict[str, dict[str, list[str]]] = {
        "admin": {"*": ["read", "write", "delete"]},
        "reviewer": {"pr": ["read", "comment"]},
    }
    role = _get_user_role(user_id)
    allowed_actions = acl.get(role, {}).get(resource) or acl.get(role, {}).get("*") or []
    return action in allowed_actions


def authorize_webhook_request(token: str, owner: str, repo: str) -> bool:
    """Authorize incoming GitHub webhook request by validating token and repo ownership."""
    payload = validate_jwt_token(token)
    if payload is None:
        raise PermissionError("Unauthorized: invalid or expired token")
    if payload.get("owner") != owner:
        raise PermissionError(f"Forbidden: token owner mismatch for repo {owner}/{repo}")
    return True


def _get_user_role(user_id: str) -> str:
    return "reviewer"


def _sign(data: str) -> str:
    return hmac.new(SECRET_KEY.encode(), data.encode(), hashlib.sha256).hexdigest()
