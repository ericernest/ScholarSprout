from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from gateway.app import app
from storage import LocalResearchStore, ResearchCatalog


class ResearchCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.store = LocalResearchStore(Path(self.directory.name) / "research.sqlite3")
        self.store.initialize()
        self.catalog = ResearchCatalog(self.store)
        self.conversation_id = self.store.create_conversation("量子控制讨论")
        self.store.append_message(
            self.conversation_id,
            role="user",
            mode="chat",
            content="先比较两种控制方法",
        )
        self.paper_id = self.store.upsert_paper(
            paper_id="paper-catalog",
            title="Reliable Quantum Control",
            authors=["Ada Lovelace"],
            abstract="A robust control method.",
            publication_year=2025,
        )
        self.reading_id = self.store.create_reading_session(
            title="Reliable Quantum Control 精读",
            paper_id=self.paper_id,
            conversation_id=self.conversation_id,
        )

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_lists_cover_conversations_readings_and_papers(self) -> None:
        self.store.add_to_library(self.paper_id, reading_status="reading", note="重点看方法")

        conversations = self.catalog.list_conversations(search="控制")
        readings = self.catalog.list_paper_readings(search="Reliable")
        papers = self.catalog.list_papers(library_only=True)

        self.assertEqual(conversations[0]["conversation_id"], self.conversation_id)
        self.assertEqual(conversations[0]["message_count"], 1)
        self.assertEqual(readings[0]["reading_session_id"], self.reading_id)
        self.assertEqual(papers[0]["reading_status"], "reading")
        self.assertEqual(papers[0]["library_note"], "重点看方法")

    def test_transient_paper_transport_conversations_are_hidden(self) -> None:
        transient = "paper-reading-transport-only"
        self.store.ensure_conversation(transient, title="论文精读：upload_paper")
        self.store.append_message(
            transient,
            role="user",
            mode="paper_reading",
            content='{"action":"upload_paper"}',
        )

        conversation_ids = {
            item["conversation_id"] for item in self.catalog.list_conversations()
        }

        self.assertNotIn(transient, conversation_ids)
        self.assertIn(self.conversation_id, conversation_ids)

    def test_annotation_round_trip_keeps_pdf_anchor_and_note(self) -> None:
        saved = self.catalog.upsert_annotation(
            annotation_id="mark-1",
            paper_id=self.paper_id,
            reading_session_id=self.reading_id,
            annotation_type="note",
            color="green",
            page_number=3,
            section_id="methods",
            selected_text="robust objective",
            rects=[{"left": 0.1, "top": 0.2, "width": 0.3, "height": 0.04}],
            note_text="检查目标函数假设",
        )

        self.assertEqual(saved["note_text"], "检查目标函数假设")
        self.assertEqual(saved["page_number"], 3)
        annotations = self.catalog.list_annotations(self.paper_id)
        self.assertEqual(annotations[0]["reading_session_id"], self.reading_id)
        self.assertEqual(annotations[0]["rects"][0]["left"], 0.1)
        self.assertTrue(self.catalog.delete_annotation(self.paper_id, "mark-1"))
        self.assertEqual(self.catalog.list_annotations(self.paper_id), [])

    def test_paper_annotations_are_in_schema(self) -> None:
        self.assertIn("paper_annotations", self.store.list_table_names())

    def test_folder_and_tags_round_trip_without_pipeline_overwrite(self) -> None:
        folder = self.catalog.create_folder("量子控制")
        saved = self.catalog.set_library_item(
            self.paper_id,
            reading_status="unread",
            note="保留这条备注",
            folder_id=folder["folder_id"],
            tags=["控制", "鲁棒性", "控制"],
        )
        self.assertTrue(saved)

        self.store.ensure_library_item(self.paper_id, reading_status="reading")
        paper = self.catalog.list_papers(library_only=True)[0]

        self.assertEqual(paper["reading_status"], "reading")
        self.assertEqual(paper["library_note"], "保留这条备注")
        self.assertEqual(paper["folder_name"], "量子控制")
        self.assertEqual(paper["tags"], ["控制", "鲁棒性"])
        self.assertEqual(self.catalog.list_papers(folder_id=folder["folder_id"])[0]["paper_id"], self.paper_id)


class ResearchLibraryApiTests(unittest.TestCase):
    def test_library_page_is_available(self) -> None:
        response = TestClient(app).get("/library")
        self.assertEqual(response.status_code, 200)
        self.assertIn("研究资料库", response.text)
        self.assertIn('id="paper-import"', response.text)
        self.assertIn('id="folder-filter"', response.text)
        self.assertIn('href="/app?new=1"', response.text)

        script = (Path(__file__).resolve().parents[2] / "gateway/static/library/app.js").read_text(encoding="utf-8")
        self.assertIn("card.tabIndex = 0", script)
        self.assertIn("function openDomainDetail", script)
        self.assertIn("function attachManagedPaper", script)
        self.assertIn("paper-record-card", script)

    def test_library_and_annotation_endpoints(self) -> None:
        with TemporaryDirectory() as directory:
            store = LocalResearchStore(Path(directory) / "research.sqlite3")
            store.initialize()
            paper_id = store.upsert_paper(title="Anchored Paper", authors=[])
            app.state.research_storage = store
            client = TestClient(app)

            library = client.put(
                f"/api/research/papers/{paper_id}/library",
                json={"reading_status": "unread", "note": "待读"},
            )
            annotation = client.put(
                f"/api/research/papers/{paper_id}/annotations/mark-api",
                json={
                    "annotation_type": "highlight",
                    "color": "yellow",
                    "page_number": 1,
                    "selected_text": "important result",
                    "rects": [{"left": 0.1, "top": 0.2, "width": 0.4, "height": 0.03}],
                },
            )
            listed = client.get(f"/api/research/papers/{paper_id}/annotations")

            self.assertEqual(library.status_code, 200)
            self.assertEqual(annotation.status_code, 200)
            self.assertEqual(listed.json()[0]["selected_text"], "important result")
            self.assertNotIn("anchor_json", listed.text)

    def test_folder_api_and_library_classification(self) -> None:
        with TemporaryDirectory() as directory:
            store = LocalResearchStore(Path(directory) / "research.sqlite3")
            store.initialize()
            paper_id = store.upsert_paper(title="Categorized Paper", authors=[])
            app.state.research_storage = store
            client = TestClient(app)

            folder = client.post("/api/research/paper-folders", json={"name": "方法论文"})
            saved = client.put(
                f"/api/research/papers/{paper_id}/library",
                json={
                    "reading_status": "unread",
                    "note": "先看实验",
                    "folder_id": folder.json()["folder_id"],
                    "tags": ["benchmark", "实验"],
                },
            )
            listed = client.get(
                "/api/research/papers",
                params={"folder_id": folder.json()["folder_id"]},
            )

            self.assertEqual(folder.status_code, 201)
            self.assertEqual(saved.status_code, 200)
            self.assertEqual(listed.json()[0]["folder_name"], "方法论文")
            self.assertEqual(listed.json()[0]["tags"], ["benchmark", "实验"])

    def test_annotation_rejects_page_overflow(self) -> None:
        with TemporaryDirectory() as directory:
            store = LocalResearchStore(Path(directory) / "research.sqlite3")
            store.initialize()
            paper_id = store.upsert_paper(title="Invalid Anchor", authors=[])
            app.state.research_storage = store
            response = TestClient(app).put(
                f"/api/research/papers/{paper_id}/annotations/mark-invalid",
                json={
                    "annotation_type": "highlight",
                    "page_number": 1,
                    "selected_text": "outside",
                    "rects": [{"left": 0.9, "top": 0.2, "width": 0.4, "height": 0.03}],
                },
            )
            self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
