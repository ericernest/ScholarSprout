from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from storage.catalog import ResearchCatalog
from storage.local_store import LocalResearchStore


class ResearchModeIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.store = LocalResearchStore(Path(self.tempdir.name) / "research.sqlite3")
        self.store.initialize()
        self.catalog = ResearchCatalog(self.store)
        self.user_a = "user-a"
        self.user_b = "user-b"
        self.conv_a = self.store.create_conversation("A", user_id=self.user_a)
        self.conv_b = self.store.create_conversation("B", user_id=self.user_b)

    def _persist_domain(self, query: str, conversation_id: str, user_id: str) -> str:
        return self.store.persist_domain_onboarding_result(
            query=query,
            conversation_id=conversation_id,
            user_id=user_id,
            response={
                "status": "ok",
                "domain": query,
                "text": f"overview-{query}",
                "papers": [],
            },
        )

    def _save_reading(self, session_id: str, paper_id: str, conversation_id: str, user_id: str) -> None:
        self.store.save_reading_session_snapshot(
            session_id,
            {
                "paper_id": paper_id,
                "paper_title": f"Paper {paper_id}",
                "conversation_id": conversation_id,
                "user_id": user_id,
                "state": "active",
                "progress": {},
                "active_skills": [],
                "checkpoints": [],
            },
        )

    def test_domain_onboarding_list_and_detail_are_scoped(self) -> None:
        artifact_a = self._persist_domain("domain-a", self.conv_a, self.user_a)
        artifact_b = self._persist_domain("domain-b", self.conv_b, self.user_b)

        items_a = self.catalog.list_domain_onboardings(user_id=self.user_a)
        self.assertEqual([item["artifact_id"] for item in items_a], [artifact_a])
        self.assertIsNotNone(self.catalog.get_domain_onboarding(artifact_a, user_id=self.user_a))
        self.assertIsNone(self.catalog.get_domain_onboarding(artifact_b, user_id=self.user_a))

    def test_provable_phase1_domain_owner_is_visible_but_unowned_legacy_is_hidden(self) -> None:
        linked = self.store.create_domain_onboarding(
            title="phase1-linked",
            query="phase1-linked",
            conversation_id=self.conv_a,
        )
        legacy = self.store.create_domain_onboarding(
            title="legacy-unowned",
            query="legacy-unowned",
        )

        visible = {item["artifact_id"] for item in self.catalog.list_domain_onboardings(user_id=self.user_a)}
        self.assertIn(linked, visible)
        self.assertNotIn(legacy, visible)
        self.assertIsNone(self.catalog.get_domain_onboarding(legacy, user_id=self.user_a))

    def test_paper_reading_list_and_access_are_scoped(self) -> None:
        self._save_reading("reading-a", "paper-a", self.conv_a, self.user_a)
        self._save_reading("reading-b", "paper-b", self.conv_b, self.user_b)

        items_a = self.catalog.list_paper_readings(user_id=self.user_a)
        self.assertEqual([item["reading_session_id"] for item in items_a], ["reading-a"])
        self.assertTrue(self.store.user_has_paper_reading_access("paper-a", self.user_a))
        self.assertFalse(self.store.user_has_paper_reading_access("paper-b", self.user_a))

    def test_explicit_paper_access_is_many_to_many_and_does_not_leak(self) -> None:
        self.store.ensure_paper_reference("shared-paper", title="Shared")
        self.assertTrue(self.store.grant_paper_reading_access("shared-paper", self.user_a, source="upload"))
        self.assertTrue(self.store.user_has_paper_reading_access("shared-paper", self.user_a))
        self.assertFalse(self.store.user_has_paper_reading_access("shared-paper", self.user_b))
        self.assertTrue(self.store.grant_paper_reading_access("shared-paper", self.user_b, source="upload"))
        self.assertTrue(self.store.user_has_paper_reading_access("shared-paper", self.user_b))

    def test_owned_domain_recommendation_can_start_reading_without_cross_user_access(self) -> None:
        artifact_a = self.store.persist_domain_onboarding_result(
            query="recommended-domain",
            conversation_id=self.conv_a,
            user_id=self.user_a,
            response={
                "status": "ok",
                "domain": "recommended-domain",
                "text": "overview",
                "papers": [
                    {
                        "paper_id": "recommended-paper",
                        "title": "Recommended Paper",
                        "authors": [],
                    }
                ],
            },
        )
        self.assertTrue(artifact_a)
        self.assertTrue(
            self.store.user_can_start_paper_reading("recommended-paper", self.user_a)
        )
        self.assertFalse(
            self.store.user_can_start_paper_reading("recommended-paper", self.user_b)
        )

    def test_counts_are_scoped_but_unscoped_local_mode_keeps_legacy_visibility(self) -> None:
        domain_a = self._persist_domain("domain-a", self.conv_a, self.user_a)
        domain_b = self._persist_domain("domain-b", self.conv_b, self.user_b)
        self._save_reading("reading-a", "paper-a", self.conv_a, self.user_a)
        self._save_reading("reading-b", "paper-b", self.conv_b, self.user_b)
        legacy_domain = self.store.create_domain_onboarding(
            title="legacy", query="legacy"
        )

        counts_a = self.catalog.counts(user_id=self.user_a)
        self.assertEqual(counts_a["domain_onboardings"], 1)
        self.assertEqual(counts_a["paper_readings"], 1)

        all_domains = {item["artifact_id"] for item in self.catalog.list_domain_onboardings()}
        self.assertEqual(all_domains, {domain_a, domain_b, legacy_domain})
        self.assertEqual(len(self.catalog.list_paper_readings()), 2)


if __name__ == "__main__":
    unittest.main()
