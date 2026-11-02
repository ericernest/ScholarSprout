from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from storage.catalog import ResearchCatalog
from storage.local_store import LocalResearchStore


class PaperLibraryIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = LocalResearchStore(Path(self.tempdir.name) / "research.sqlite3")
        self.store.initialize()
        self.catalog = ResearchCatalog(self.store)
        self.paper_id = self.store.upsert_paper(
            paper_id="paper-shared",
            title="Shared canonical paper",
            authors=["Researcher"],
        )
        for user_id in ("user-a", "user-b"):
            self.store.grant_paper_reading_access(
                self.paper_id, user_id, source="test"
            )
            self.store.ensure_library_item(self.paper_id, user_id=user_id)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_same_canonical_paper_has_private_library_state(self) -> None:
        folder_a = self.catalog.create_folder("A only", user_id="user-a")
        self.assertTrue(
            self.catalog.set_library_item(
                self.paper_id,
                reading_status="read",
                note="private library note A",
                folder_id=folder_a["folder_id"],
                user_id="user-a",
            )
        )
        papers_a = self.catalog.list_papers(user_id="user-a")
        papers_b = self.catalog.list_papers(user_id="user-b")
        self.assertEqual(papers_a[0]["library_note"], "private library note A")
        self.assertEqual(papers_a[0]["folder_name"], "A only")
        self.assertEqual(papers_b[0]["library_note"], "")
        self.assertEqual(papers_b[0]["folder_name"], "")
        self.assertEqual(papers_b[0]["reading_status"], "unread")

    def test_folders_are_owner_scoped(self) -> None:
        folder_a = self.catalog.create_folder("Only A", user_id="user-a")
        folder_b = self.catalog.create_folder("Only B", user_id="user-b")
        self.assertEqual(
            [item["folder_id"] for item in self.catalog.list_folders(user_id="user-a")],
            [folder_a["folder_id"]],
        )
        self.assertEqual(
            [item["folder_id"] for item in self.catalog.list_folders(user_id="user-b")],
            [folder_b["folder_id"]],
        )
        with self.assertRaises(ValueError):
            self.catalog.move_library_item(
                self.paper_id,
                folder_id=folder_a["folder_id"],
                user_id="user-b",
            )
        self.assertIsNone(
            self.catalog.update_folder(
                folder_a["folder_id"],
                name="takeover",
                parent_folder_id=None,
                user_id="user-b",
            )
        )

    def test_paper_notes_are_private(self) -> None:
        note_a = self.catalog.set_paper_note(
            self.paper_id, "A private note", user_id="user-a"
        )
        self.assertEqual(note_a["content_markdown"], "A private note")
        self.assertEqual(
            self.catalog.get_paper_note(self.paper_id, user_id="user-b")[
                "content_markdown"
            ],
            "",
        )
        note_b = self.catalog.set_paper_note(
            self.paper_id, "B private note", user_id="user-b"
        )
        self.assertEqual(note_b["content_markdown"], "B private note")
        self.assertEqual(
            self.catalog.get_paper_note(self.paper_id, user_id="user-a")[
                "content_markdown"
            ],
            "A private note",
        )

    def test_annotations_are_private_even_with_same_annotation_id(self) -> None:
        for user_id, text in (("user-a", "A secret"), ("user-b", "B secret")):
            item = self.catalog.upsert_annotation(
                annotation_id="shared-client-id",
                paper_id=self.paper_id,
                reading_session_id=None,
                annotation_type="highlight",
                color="yellow",
                page_number=1,
                section_id=None,
                selected_text=text,
                rects=[],
                note_text=f"note {user_id}",
                user_id=user_id,
            )
            self.assertIsNotNone(item)
        self.assertEqual(
            [item["selected_text"] for item in self.catalog.list_annotations(
                self.paper_id, user_id="user-a"
            )],
            ["A secret"],
        )
        self.assertEqual(
            [item["selected_text"] for item in self.catalog.list_annotations(
                self.paper_id, user_id="user-b"
            )],
            ["B secret"],
        )
        self.assertTrue(
            self.catalog.delete_annotation(
                self.paper_id, "shared-client-id", user_id="user-b"
            )
        )
        self.assertEqual(len(self.catalog.list_annotations(self.paper_id, user_id="user-a")), 1)

    def test_legacy_global_paper_state_is_hidden_from_hosted_users(self) -> None:
        legacy_paper = self.store.upsert_paper(
            paper_id="paper-legacy", title="Legacy test", authors=[]
        )
        self.store.ensure_library_item(legacy_paper, reading_status="read")
        self.catalog.set_paper_note(legacy_paper, "legacy global note")
        self.assertEqual(
            [item["paper_id"] for item in self.catalog.list_papers(user_id="user-a")],
            [self.paper_id],
        )
        self.assertIsNone(
            self.catalog.get_paper_note(legacy_paper, user_id="user-a")
        )
        self.assertIn(
            legacy_paper,
            [item["paper_id"] for item in self.catalog.list_papers(user_id=None)],
        )

    def test_removed_private_library_item_is_not_recreated_on_restart(self) -> None:
        self.assertTrue(
            self.catalog.remove_library_item(self.paper_id, user_id="user-a")
        )
        self.store.initialize()
        self.assertEqual(self.catalog.list_papers(user_id="user-a", library_only=True), [])
        self.assertEqual(len(self.catalog.list_papers(user_id="user-b", library_only=True)), 1)

    def test_scoped_counts_do_not_include_other_users_private_state(self) -> None:
        extra = self.store.upsert_paper(
            paper_id="paper-a-extra", title="A extra", authors=[]
        )
        self.store.grant_paper_reading_access(extra, "user-a", source="test")
        self.store.ensure_library_item(extra, user_id="user-a")
        counts_a = self.catalog.counts(user_id="user-a")
        counts_b = self.catalog.counts(user_id="user-b")
        self.assertEqual(counts_a["library_papers"], 2)
        self.assertEqual(counts_b["library_papers"], 1)
        self.assertEqual(counts_a["all_papers"], 2)
        self.assertEqual(counts_b["all_papers"], 1)


if __name__ == "__main__":
    unittest.main()
