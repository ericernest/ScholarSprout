"""Application services for email OTP, GitHub OAuth and opaque server sessions."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import smtplib
import ssl
import time
from email.message import EmailMessage
from urllib.parse import urlencode

import httpx

from .storage import AuthStore


class AuthConfigurationError(RuntimeError):
    pass


class AuthService:
    def __init__(self, database_path: str) -> None:
        self.enabled = os.getenv("AUTH_ENABLED", "0").strip().lower() in {"1", "true", "yes"}
        self.secret = os.getenv("AUTH_SECRET_KEY", "").encode("utf-8")
        if self.enabled and len(self.secret) < 32:
            raise AuthConfigurationError(
                "AUTH_ENABLED=1 requires AUTH_SECRET_KEY with at least 32 characters."
            )
        if not self.secret:
            self.secret = secrets.token_bytes(32)
        self.store = AuthStore(database_path)
        self.store.initialize()
        self.session_seconds = int(os.getenv("AUTH_SESSION_DAYS", "14")) * 86400
        self.cookie_secure = os.getenv("AUTH_COOKIE_SECURE", "1").strip().lower() not in {"0", "false", "no"}
        self.cookie_name = "ss_session"

    def require_enabled(self) -> None:
        if not self.enabled:
            raise AuthConfigurationError(
                "Website authentication is disabled. Set AUTH_ENABLED=1 and configure its environment."
            )

    def _digest(self, purpose: str, value: str) -> str:
        return hmac.new(
            self.secret, f"{purpose}:{value}".encode("utf-8"), hashlib.sha256
        ).hexdigest()

    @staticmethod
    def normalize_email(email: str) -> str:
        normalized = email.strip().casefold()
        if len(normalized) > 254 or normalized.count("@") != 1:
            raise ValueError("Please enter a valid email address.")
        local, domain = normalized.rsplit("@", 1)
        if not local or not domain or "." not in domain or any(ch.isspace() for ch in normalized):
            raise ValueError("Please enter a valid email address.")
        return normalized

    def request_email_code(self, email: str, request_ip: str) -> None:
        self.require_enabled()
        email = self.normalize_email(email)

        now = int(time.time())

        smtp_host = os.getenv("SMTP_HOST", "").strip()
        smtp_from = os.getenv("SMTP_FROM", "").strip()

        if not smtp_host or not smtp_from:
            raise AuthConfigurationError(
                "SMTP_HOST and SMTP_FROM must be configured before email login."
            )

        code = f"{secrets.randbelow(1_000_000):06d}"
        code_hash = self._digest("email-otp", f"{email}:{code}")

        # 原子地检查发送频率并保存验证码。
        # 邮箱60秒内最多请求一次，单个IP每小时最多请求10次。
        allowed = self.store.add_otp_if_allowed(
            email=email,
            code_hash=code_hash,
            now=now,
            expires_at=now + 600,
            request_ip=request_ip,
        )

        if not allowed:
            raise RuntimeError(
                "Too many verification-code requests. Please wait before trying again."
            )
        message = EmailMessage()
        message["Subject"] = "Your ScholarSprout verification code"
        message["From"] = smtp_from
        message["To"] = email
        message.set_content(
            f"Your ScholarSprout verification code is {code}. "
            "It expires in 10 minutes and can only be used once. "
            "If you did not request this code, you can ignore this email."
        )
        try:
            port = int(os.getenv("SMTP_PORT", "587"))
            username = os.getenv("SMTP_USERNAME", "")
            password = os.getenv("SMTP_PASSWORD", "")
            timeout = float(os.getenv("SMTP_TIMEOUT", "15"))
            if port == 465:
                with smtplib.SMTP_SSL(
                    smtp_host, port, timeout=timeout, context=ssl.create_default_context()
                ) as client:
                    if username:
                        client.login(username, password)
                    client.send_message(message)
            else:
                with smtplib.SMTP(smtp_host, port, timeout=timeout) as client:
                    client.ehlo()
                    client.starttls(context=ssl.create_default_context())
                    client.ehlo()
                    if username:
                        client.login(username, password)
                    client.send_message(message)
        except Exception:
            self.store.invalidate_latest_otp(email, int(time.time()))
            raise

    def verify_email_code(self, email: str, code: str) -> tuple[dict, str]:
        self.require_enabled()
        email = self.normalize_email(email)
        if not code.isdigit() or len(code) != 6:
            raise ValueError("The verification code is invalid or expired.")
        now = int(time.time())
        candidate_hash = self._digest("email-otp", f"{email}:{code}")
        if not self.store.verify_otp(email, candidate_hash, now):
            raise ValueError("The verification code is invalid or expired.")
        user = self.store.get_or_create_identity_user(
            "email", email, email, True, email.split("@", 1)[0], now
        )
        return user, self.create_session(user["user_id"])

    def create_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(48)
        now = int(time.time())
        self.store.create_session(
            user_id, self._digest("session", token), now, now + self.session_seconds
        )
        return token

    def current_user(self, token: str | None) -> dict | None:
        if not token:
            return None
        return self.store.get_session_user(self._digest("session", token), int(time.time()))

    def revoke_session(self, token: str | None) -> None:
        if token:
            self.store.revoke_session(self._digest("session", token), int(time.time()))

    def github_authorize_url(self) -> tuple[str, str]:
        self.require_enabled()
        client_id = os.getenv("GITHUB_CLIENT_ID", "").strip()
        callback = os.getenv("GITHUB_CALLBACK_URL", "").strip()
        if not client_id or not callback:
            raise AuthConfigurationError("GITHUB_CLIENT_ID and GITHUB_CALLBACK_URL are required.")
        state = secrets.token_urlsafe(32)
        now = int(time.time())
        self.store.add_oauth_state(self._digest("oauth-state", state), now, now + 600)
        query = urlencode({
            "client_id": client_id,
            "redirect_uri": callback,
            "scope": "read:user user:email",
            "state": state,
        })
        return f"https://github.com/login/oauth/authorize?{query}", state

    async def github_callback(self, code: str, state: str, state_cookie: str) -> tuple[dict, str]:
        self.require_enabled()
        if not state or not state_cookie or not hmac.compare_digest(state, state_cookie):
            raise ValueError("GitHub authorization state validation failed.")
        now = int(time.time())
        if not self.store.consume_oauth_state(self._digest("oauth-state", state), now):
            raise ValueError("GitHub authorization state expired or was already used.")
        client_id = os.getenv("GITHUB_CLIENT_ID", "").strip()
        client_secret = os.getenv("GITHUB_CLIENT_SECRET", "").strip()
        if not client_id or not client_secret:
            raise AuthConfigurationError("GitHub OAuth credentials are not configured.")
        timeout = httpx.Timeout(15.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            token_response = await client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "redirect_uri": os.getenv("GITHUB_CALLBACK_URL", "").strip(),
                },
            )
            token_response.raise_for_status()
            access_token = token_response.json().get("access_token")
            if not access_token:
                raise ValueError("GitHub did not return an access token.")
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            user_response = await client.get("https://api.github.com/user", headers=headers)
            user_response.raise_for_status()
            profile = user_response.json()
            github_id = str(profile.get("id") or "")
            if not github_id:
                raise ValueError("GitHub account ID was missing.")
            email = None
            email_verified = False
            emails_response = await client.get("https://api.github.com/user/emails", headers=headers)
            if emails_response.is_success:
                for item in emails_response.json():
                    if item.get("primary") and item.get("verified"):
                        email = item.get("email")
                        email_verified = True
                        break
        user = self.store.get_or_create_identity_user(
            "github",
            github_id,
            email,
            email_verified,
            str(profile.get("name") or profile.get("login") or "GitHub user"),
            now,
        )
        return user, self.create_session(user["user_id"])
