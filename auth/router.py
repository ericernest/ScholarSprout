"""HTTP endpoints for website authentication."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from urllib.parse import urlsplit

from .service import AuthConfigurationError, AuthService

import logging
logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/auth", tags=["authentication"])
SESSION_COOKIE = "ss_session"
OAUTH_STATE_COOKIE = "ss_github_state"


class EmailCodeRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class EmailCodeVerify(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    code: str = Field(min_length=6, max_length=6)


def get_auth_service(request: Request) -> AuthService:
    service = getattr(request.app.state, "auth_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Authentication service is not initialized.")
    try:
        service.require_enabled()
    except AuthConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return service


def get_current_user(request: Request, service: AuthService = Depends(get_auth_service)) -> dict:
    user = service.current_user(request.cookies.get(SESSION_COOKIE))
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return user


def _check_same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin:
        return
    parsed = urlsplit(origin)
    expected_host = request.headers.get("host", request.url.netloc).lower()
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != expected_host:
        raise HTTPException(status_code=403, detail="Cross-origin request rejected.")


def _set_session_cookie(response: Response, service: AuthService, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=service.session_seconds,
        httponly=True,
        secure=service.cookie_secure,
        samesite="lax",
        path="/",
    )


@router.post("/email/request", status_code=202)
def request_email_code(
    payload: EmailCodeRequest,
    request: Request,
    service: AuthService = Depends(get_auth_service),
) -> dict[str, str]:
    _check_same_origin(request)
    ip = request.client.host if request.client else "unknown"
    try:
        service.request_email_code(payload.email, ip)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except AuthConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=429, detail=str(error)) from error
    except Exception:
        logger.exception("SMTP verification email delivery failed")

        raise HTTPException(
            status_code=502,
            detail="Unable to send the verification email."
        )
    return {"message": "If the address can receive mail, a verification code has been sent."}


@router.post("/email/verify")
def verify_email_code(
    payload: EmailCodeVerify,
    request: Request,
    response: Response,
    service: AuthService = Depends(get_auth_service),
) -> dict:
    _check_same_origin(request)
    try:
        user, token = service.verify_email_code(payload.email, payload.code)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except PermissionError as error:
        raise HTTPException(status_code=403, detail="This account is disabled.") from error
    _set_session_cookie(response, service, token)
    return {"authenticated": True, "user": user}


@router.get("/github/start")
def github_start(
    service: AuthService = Depends(get_auth_service),
) -> RedirectResponse:
    try:
        url, state = service.github_authorize_url()
    except AuthConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        key=OAUTH_STATE_COOKIE,
        value=state,
        max_age=600,
        httponly=True,
        secure=service.cookie_secure,
        samesite="lax",
        path="/",
    )
    return response


@router.get("/github/callback")
async def github_callback(
    request: Request,
    code: str = "",
    state: str = "",
    service: AuthService = Depends(get_auth_service),
) -> RedirectResponse:
    if not code:
        response = RedirectResponse("/login?error=github_denied", status_code=303)
        response.delete_cookie(OAUTH_STATE_COOKIE, path="/")
        return response
    state_cookie = request.cookies.get(OAUTH_STATE_COOKIE, "")
    try:
        user, token = await service.github_callback(code, state, state_cookie)
    except (ValueError, AuthConfigurationError, PermissionError):
        response = RedirectResponse("/login?error=github_failed", status_code=303)
        response.delete_cookie(OAUTH_STATE_COOKIE, path="/")
        return response
    response = RedirectResponse("/", status_code=303)
    _set_session_cookie(response, service, token)
    response.delete_cookie(OAUTH_STATE_COOKIE, path="/")
    return response


@router.get("/me")
def read_current_user(
    request: Request,
    service: AuthService = Depends(get_auth_service),
) -> dict:
    user = service.current_user(request.cookies.get(SESSION_COOKIE))
    return {"authenticated": user is not None, "user": user}


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    service: AuthService = Depends(get_auth_service),
) -> dict[str, bool]:
    _check_same_origin(request)
    service.revoke_session(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(
        key=SESSION_COOKIE,
        path="/",
        httponly=True,
        secure=service.cookie_secure,
        samesite="lax",
    )
    return {"authenticated": False}
