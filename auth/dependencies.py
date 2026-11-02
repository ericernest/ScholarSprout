"""Trusted research identity helpers for web requests.

Hosted mode (AUTH_ENABLED=1) always requires a valid server session.
Local mode preserves the pre-auth single-user behavior.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from .router import SESSION_COOKIE
from .service import AuthService

LOCAL_WEB_USER_ID = "local-web"


def _auth_service(request: Request) -> AuthService:
    service = getattr(request.app.state, "auth_service", None)
    if not isinstance(service, AuthService):
        raise HTTPException(
            status_code=503, detail="Authentication service is not initialized."
        )
    return service


def resolve_research_user(request: Request) -> dict:
    """Return the trusted user identity used by research writes."""
    service = _auth_service(request)
    if not service.enabled:
        return {
            "user_id": LOCAL_WEB_USER_ID,
            "display_name": "Local Web User",
            "auth_mode": "local",
        }
    user = service.current_user(request.cookies.get(SESSION_COOKIE))
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    user_id = str(user.get("user_id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return user


def resolve_research_user_id(request: Request) -> str:
    return str(resolve_research_user(request)["user_id"])


def resolve_research_scope_user_id(request: Request) -> str | None:
    """Return the owner filter for reads. None intentionally means local legacy scope."""
    service = _auth_service(request)
    if not service.enabled:
        return None
    return resolve_research_user_id(request)
