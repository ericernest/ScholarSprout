"""SQLite persistence for web identities, one-time codes, OAuth state and sessions."""

from __future__ import annotations

import hmac
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from uuid import uuid4


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class AuthStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def initialize(self) -> None:
        with self.connection() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS auth_users (
                user_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL,
                disabled_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS auth_identities (
                identity_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES auth_users(user_id) ON DELETE CASCADE,
                provider TEXT NOT NULL CHECK(provider IN ('email', 'github')),
                provider_subject TEXT NOT NULL,
                email TEXT,
                email_verified INTEGER NOT NULL DEFAULT 0 CHECK(email_verified IN (0,1)),
                created_at INTEGER NOT NULL,
                UNIQUE(provider, provider_subject)
            );
            CREATE INDEX IF NOT EXISTS idx_auth_identities_user
                ON auth_identities(user_id);
            CREATE TABLE IF NOT EXISTS auth_email_otps (
                otp_id TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                code_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                request_ip TEXT NOT NULL DEFAULT '',
                attempts INTEGER NOT NULL DEFAULT 0,
                consumed_at INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_auth_email_otps_email_created
                ON auth_email_otps(email, created_at DESC);
            CREATE TABLE IF NOT EXISTS auth_sessions (
                session_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES auth_users(user_id) ON DELETE CASCADE,
                token_hash TEXT NOT NULL UNIQUE,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                revoked_at INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_auth_sessions_token
                ON auth_sessions(token_hash);
            CREATE TABLE IF NOT EXISTS auth_oauth_states (
                state_hash TEXT PRIMARY KEY,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                consumed_at INTEGER
            );
            """)

    def add_otp_if_allowed(
        self, email: str, code_hash: str, now: int,
        expires_at: int, request_ip: str,
    ) -> bool:
        # Rate-limit check and insert share one transaction to avoid concurrent
        # requests bypassing the per-email/per-IP limits.
        with self.connection() as db:
            recent_email = db.execute(
                """SELECT COUNT(*) FROM auth_email_otps
                   WHERE email=? AND created_at>?""", (email, now - 60)
            ).fetchone()[0]
            recent_ip = db.execute(
                """SELECT COUNT(*) FROM auth_email_otps
                   WHERE request_ip=? AND created_at>?""", (request_ip, now - 3600)
            ).fetchone()[0]
            if recent_email != 0 or recent_ip >= 10:
                return False
            db.execute(
                """INSERT INTO auth_email_otps
                   (otp_id,email,code_hash,created_at,expires_at,request_ip)
                   VALUES(?,?,?,?,?,?)""",
                (_new_id("otp"), email, code_hash, now, expires_at, request_ip),
            )
            return True

    def invalidate_latest_otp(self, email: str, now: int) -> None:
        with self.connection() as db:
            db.execute(
                """UPDATE auth_email_otps SET consumed_at=?
                   WHERE otp_id=(SELECT otp_id FROM auth_email_otps
                     WHERE email=? AND consumed_at IS NULL
                     ORDER BY created_at DESC LIMIT 1)""", (now, email)
            )

    def verify_otp(self, email: str, candidate_hash: str, now: int) -> bool:
        with self.connection() as db:
            row = db.execute(
                """SELECT otp_id,code_hash,expires_at,attempts FROM auth_email_otps
                   WHERE email=? AND consumed_at IS NULL
                   ORDER BY created_at DESC LIMIT 1""", (email,)
            ).fetchone()
            if row is None or row["expires_at"] < now or row["attempts"] >= 5:
                return False
            if not hmac.compare_digest(row["code_hash"], candidate_hash):
                db.execute(
                    "UPDATE auth_email_otps SET attempts=attempts+1 WHERE otp_id=?",
                    (row["otp_id"],),
                )
                return False
            db.execute(
                "UPDATE auth_email_otps SET consumed_at=? WHERE otp_id=?",
                (now, row["otp_id"]),
            )
            return True

    def get_or_create_identity_user(
        self, provider: str, subject: str, email: str | None,
        email_verified: bool, display_name: str, now: int,
    ) -> dict:
        with self.connection() as db:
            identity = db.execute(
                """SELECT u.user_id,u.display_name,u.created_at,u.disabled_at
                   FROM auth_identities i JOIN auth_users u ON u.user_id=i.user_id
                   WHERE i.provider=? AND i.provider_subject=?""",
                (provider, subject),
            ).fetchone()
            if identity:
                if identity["disabled_at"] is not None:
                    raise PermissionError("This account is disabled.")
                if display_name:
                    db.execute(
                        "UPDATE auth_users SET display_name=? WHERE user_id=?",
                        (display_name, identity["user_id"]),
                    )
                return dict(db.execute(
                    "SELECT user_id,display_name,created_at FROM auth_users WHERE user_id=?",
                    (identity["user_id"],),
                ).fetchone())
            user_id = _new_id("usr")
            db.execute(
                "INSERT INTO auth_users(user_id,display_name,created_at) VALUES(?,?,?)",
                (user_id, display_name or "", now),
            )
            db.execute(
                """INSERT INTO auth_identities
                   (identity_id,user_id,provider,provider_subject,email,email_verified,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (_new_id("idn"), user_id, provider, subject, email,
                 1 if email_verified else 0, now),
            )
            return {"user_id": user_id, "display_name": display_name or "", "created_at": now}

    def create_session(self, user_id: str, token_hash: str, now: int, expires_at: int) -> None:
        with self.connection() as db:
            db.execute(
                """INSERT INTO auth_sessions
                   (session_id,user_id,token_hash,created_at,expires_at)
                   VALUES(?,?,?,?,?)""",
                (_new_id("ses"), user_id, token_hash, now, expires_at),
            )

    def get_session_user(self, token_hash: str, now: int) -> dict | None:
        with self.connection() as db:
            row = db.execute(
                """SELECT u.user_id,u.display_name,u.created_at
                   FROM auth_sessions s JOIN auth_users u ON u.user_id=s.user_id
                   WHERE s.token_hash=? AND s.revoked_at IS NULL
                     AND s.expires_at>? AND u.disabled_at IS NULL""",
                (token_hash, now),
            ).fetchone()
            return dict(row) if row else None

    def revoke_session(self, token_hash: str, now: int) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE auth_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                (now, token_hash),
            )

    def add_oauth_state(self, state_hash: str, now: int, expires_at: int) -> None:
        with self.connection() as db:
            db.execute(
                "INSERT INTO auth_oauth_states(state_hash,created_at,expires_at) VALUES(?,?,?)",
                (state_hash, now, expires_at),
            )

    def consume_oauth_state(self, state_hash: str, now: int) -> bool:
        with self.connection() as db:
            row = db.execute(
                """SELECT expires_at,consumed_at FROM auth_oauth_states
                   WHERE state_hash=?""", (state_hash,)
            ).fetchone()
            if row is None or row["consumed_at"] is not None or row["expires_at"] < now:
                return False
            db.execute(
                "UPDATE auth_oauth_states SET consumed_at=? WHERE state_hash=?",
                (now, state_hash),
            )
            return True
