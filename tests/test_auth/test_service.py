from __future__ import annotations

import hashlib
import hmac
import os
import tempfile
import unittest
from pathlib import Path

from auth.service import AuthService


class AuthServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.previous = {
            key: os.environ.get(key)
            for key in ("AUTH_ENABLED", "AUTH_SECRET_KEY", "AUTH_COOKIE_SECURE")
        }
        os.environ["AUTH_ENABLED"] = "1"
        os.environ["AUTH_SECRET_KEY"] = "test-secret-key-that-is-at-least-32-characters"
        os.environ["AUTH_COOKIE_SECURE"] = "0"
        self.service = AuthService(str(Path(self.temp.name) / "auth.sqlite3"))

    def tearDown(self) -> None:
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp.cleanup()

    def test_email_otp_creates_session_and_user(self) -> None:
        email = "person@example.com"
        code = "123456"
        now = 1_900_000_000
        code_hash = self.service._digest("email-otp", f"{email}:{code}")
        self.assertTrue(
            self.service.store.add_otp_if_allowed(
                email, code_hash, now, now + 600, "127.0.0.1"
            )
        )
        # Use the store clock boundary by temporarily patching the module clock.
        import auth.service as service_module
        original_time = service_module.time.time
        service_module.time.time = lambda: now + 1
        try:
            user, token = self.service.verify_email_code(email, code)
            self.assertTrue(token)
            self.assertEqual(self.service.current_user(token)["user_id"], user["user_id"])
            self.service.revoke_session(token)
            self.assertIsNone(self.service.current_user(token))
        finally:
            service_module.time.time = original_time


if __name__ == "__main__":
    unittest.main()
