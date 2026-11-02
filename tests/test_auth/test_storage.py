from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from auth.storage import AuthStore


class AuthStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = AuthStore(Path(self.temp.name) / "research.sqlite3")
        self.store.initialize()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_email_identity_creates_one_user(self) -> None:
        first = self.store.get_or_create_identity_user(
            "email", "person@example.com", "person@example.com", True, "person", 100
        )
        second = self.store.get_or_create_identity_user(
            "email", "person@example.com", "person@example.com", True, "person", 101
        )
        self.assertEqual(first["user_id"], second["user_id"])

    def test_github_identity_creates_distinct_identity(self) -> None:
        email_user = self.store.get_or_create_identity_user(
            "email", "person@example.com", "person@example.com", True, "person", 100
        )
        github_user = self.store.get_or_create_identity_user(
            "github", "12345", "person@example.com", True, "Person", 101
        )
        # Same email does not silently merge two authentication identities.
        self.assertNotEqual(email_user["user_id"], github_user["user_id"])

    def test_session_can_be_read_and_revoked(self) -> None:
        user = self.store.get_or_create_identity_user(
            "email", "person@example.com", "person@example.com", True, "person", 100
        )
        self.store.create_session(user["user_id"], "token-digest", 100, 200)
        self.assertEqual(
            self.store.get_session_user("token-digest", 150)["user_id"], user["user_id"]
        )
        self.store.revoke_session("token-digest", 160)
        self.assertIsNone(self.store.get_session_user("token-digest", 170))

    def test_otp_attempts_are_limited(self) -> None:
        self.assertTrue(self.store.add_otp_if_allowed("person@example.com", "expected-digest", 100, 200, "127.0.0.1"))
        for now in range(4):
            self.assertFalse(
                self.store.verify_otp("person@example.com", "wrong-digest", 101 + now)
            )
        self.assertTrue(
            self.store.verify_otp("person@example.com", "expected-digest", 110)
        )
        self.assertFalse(
            self.store.verify_otp("person@example.com", "expected-digest", 111)
        )


if __name__ == "__main__":
    unittest.main()
