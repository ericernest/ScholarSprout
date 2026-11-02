from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from storage.catalog import ResearchCatalog
from storage.local_store import LocalResearchStore


class ConversationIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = LocalResearchStore(Path(self.temp.name) / "research.sqlite3")
        self.store.initialize()
        self.catalog = ResearchCatalog(self.store)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _conversation(self, title: str, user_id: str) -> str:
        conversation_id = self.store.create_conversation(title, user_id=user_id)
        self.store.append_message(
            conversation_id, role="user", content=title, message_id=f"msg-{conversation_id}"
        )
        return conversation_id

    def test_catalog_reads_are_scoped_by_owner(self) -> None:
        a = self._conversation("A conversation", "user-a")
        b = self._conversation("B conversation", "user-b")
        self.assertEqual(
            [item["conversation_id"] for item in self.catalog.list_conversations(user_id="user-a")],
            [a],
        )
        self.assertEqual(
            [item["conversation_id"] for item in self.catalog.list_conversations(user_id="user-b")],
            [b],
        )
        self.assertIsNotNone(self.catalog.get_conversation(a, user_id="user-a"))
        self.assertIsNone(self.catalog.get_conversation(a, user_id="user-b"))
        self.assertEqual(self.catalog.counts(user_id="user-a")["conversations"], 1)
        self.assertEqual(self.catalog.counts(user_id="user-b")["conversations"], 1)

    def test_existing_conversation_cannot_be_taken_over(self) -> None:
        conversation_id = self._conversation("Owned by A", "user-a")
        with self.assertRaises(KeyError):
            self.store.ensure_conversation(
                conversation_id, title="attempted takeover", user_id="user-b"
            )
        # Local compatibility may touch last_active_at, but must never rewrite owner.
        self.store.ensure_conversation(
            conversation_id, title="local access", user_id="local-web"
        )
        with self.store._connection() as connection:
            row = connection.execute(
                "SELECT user_id FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        self.assertEqual(row["user_id"], "user-a")

    def test_foreign_parent_cannot_be_forked(self) -> None:
        parent = self._conversation("A parent", "user-a")
        with self.assertRaises(KeyError):
            self.store.create_conversation(
                "B child", parent_conversation_id=parent, user_id="user-b"
            )

    def test_context_candidates_and_links_reject_foreign_artifact(self) -> None:
        a = self._conversation("A", "user-a")
        b = self._conversation("B", "user-b")
        self.store.save_reading_session_snapshot(
            "reading-a",
            {
                "paper_id": "paper-a",
                "paper_title": "Paper A",
                "conversation_id": a,
                "user_id": "user-a",
                "state": "active",
                "progress": {},
                "active_skills": [],
                "checkpoints": [],
            },
        )
        artifact_id = "paper-reading:reading-a"
        a_candidates = self.catalog.list_context_candidates(
            conversation_id=a, user_id="user-a"
        )
        self.assertIn(artifact_id, {item["artifact_id"] for item in a_candidates})
        b_candidates = self.catalog.list_context_candidates(
            conversation_id=b, user_id="user-b"
        )
        self.assertNotIn(artifact_id, {item["artifact_id"] for item in b_candidates})
        self.assertEqual(
            self.store.link_research_artifacts(
                b, [artifact_id], user_id="user-b"
            ),
            [],
        )

    def test_unscoped_local_mode_preserves_legacy_visibility(self) -> None:
        a = self._conversation("A", "user-a")
        b = self._conversation("B", "user-b")
        self.assertEqual(
            {item["conversation_id"] for item in self.catalog.list_conversations(user_id=None)},
            {a, b},
        )


if __name__ == "__main__":
    unittest.main()
