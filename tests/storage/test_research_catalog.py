from __future__ import annotations

import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from gateway.app import app
from handlers.paper_reading.harness.session import SessionManager
from storage import LocalResearchStore, PaperReadingStorage, ResearchCatalog


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
        self.assertEqual(conversations[0]["workspace_kind"], "paper_reading")
        self.assertEqual(conversations[0]["reading_session_id"], self.reading_id)
        self.assertEqual(readings[0]["reading_session_id"], self.reading_id)
        self.assertEqual(readings[0]["paper_abstract"], "A robust control method.")
        self.assertEqual(papers[0]["reading_status"], "reading")
        self.assertEqual(papers[0]["library_note"], "重点看方法")

        detail = self.catalog.get_conversation(self.conversation_id)
        self.assertEqual(detail["workspace_kind"], "paper_reading")
        self.assertEqual(detail["reading_session_id"], self.reading_id)
        self.assertEqual(detail["paper_id"], self.paper_id)

    def test_user_facing_counts_and_lists_hide_internal_or_duplicate_records(self) -> None:
        latest_reading = self.store.create_reading_session(
            title="Reliable Quantum Control 再次精读",
            paper_id=self.paper_id,
            conversation_id=self.conversation_id,
        )
        self.store.ensure_conversation("empty-internal", title="Internal")
        first_domain = self.store.persist_domain_onboarding_result(
            query="  Quantum   Control ",
            conversation_id=self.conversation_id,
            response={"status": "ok", "domain": "Quantum Control", "text": "first"},
        )
        second_domain = self.store.persist_domain_onboarding_result(
            query="quantum control",
            conversation_id=self.conversation_id,
            response={"status": "ok", "domain": "Quantum Control", "text": "second"},
        )

        counts = self.catalog.counts()
        readings = self.catalog.list_paper_readings()
        domains = self.catalog.list_domain_onboardings()

        self.assertEqual(counts["conversations"], 1)
        self.assertEqual(counts["paper_readings"], 1)
        self.assertEqual(len(readings), 1)
        self.assertEqual(readings[0]["reading_session_id"], latest_reading)
        self.assertEqual(counts["domain_onboardings"], 1)
        self.assertEqual(len(domains), 1)
        self.assertEqual(domains[0]["artifact_id"], second_domain)
        self.assertNotEqual(first_domain, second_domain)

    def test_conversation_titles_hide_paper_reading_prefix(self) -> None:
        conversation_id = self.store.create_conversation("论文精读：AMEM：Agentic Memory")
        self.store.append_message(conversation_id, role="user", content="开始讨论", mode="chat")

        listed = next(item for item in self.catalog.list_conversations() if item["conversation_id"] == conversation_id)
        detail = self.catalog.get_conversation(conversation_id)

        self.assertEqual(listed["title"], "AMEM：Agentic Memory")
        self.assertEqual(detail["title"], "AMEM：Agentic Memory")

    def test_paper_cards_normalize_structured_text_and_author_objects(self) -> None:
        malformed_id = self.store.upsert_paper(
            paper_id="paper-malformed-card",
            title=(
                "{'title': 'A Clean Card Title', "
                "'abstract': 'The abstract belongs in the card body, not its title.'}"
            ),
            authors=["{'name': 'Alice Example', 'affiliation': 'Example Lab'}"],
        )
        self.store.add_to_library(malformed_id)

        paper = next(
            item for item in self.catalog.list_papers(library_only=True)
            if item["paper_id"] == malformed_id
        )

        self.assertEqual(paper["title"], "A Clean Card Title")
        self.assertEqual(
            paper["abstract"],
            "The abstract belongs in the card body, not its title.",
        )
        self.assertEqual(paper["authors"], ["Alice Example"])

    def test_paper_cards_repair_persisted_ieee_running_header(self) -> None:
        paper_id = "paper-ieee-header"
        bad_title = "IEEE TRANSACTIONS ON KNOWLEDGE AND DATA ENGINEERING, VOL. XX, NO. X, 2026"
        self.store.save_paper_document(
            paper_id,
            {
                "paper_id": paper_id,
                "title": bad_title,
                "abstract": "A survey abstract.",
                "authors": [],
                "full_text": (
                    f"{bad_title}\n1\nSelf-Evolving Agents as Dynamic Graph\n"
                    "Transformation: A Survey and New Perspective\nAlice Example, Bob Example\nAbstract"
                ),
                "sections": [],
            },
        )
        self.store.add_to_library(paper_id)

        paper = next(item for item in self.catalog.list_papers() if item["paper_id"] == paper_id)

        self.assertEqual(
            paper["title"],
            "Self-Evolving Agents as Dynamic Graph Transformation: A Survey and New Perspective",
        )

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

    def test_domain_count_does_not_treat_evidence_as_recommendations(self) -> None:
        task_id = "domain-evidence-fallback"
        self.store.create_domain_onboarding(
            artifact_id=task_id,
            title="领域入门：证据论文回退",
            query="证据论文回退",
        )
        self.store.persist_domain_onboarding_result(
            artifact_id=task_id,
            query="证据论文回退",
            response={
                "status": "ok",
                "papers": [
                    {"paper_id": "stale-recommendation", "title": "Stale Recommendation"}
                ],
            },
        )
        evidence_papers = [
            {"paper_id": "evidence-1", "title": "Evidence One"},
            {"paper_id": "evidence-2", "title": "Evidence Two"},
        ]
        with self.store._connection() as connection:
            connection.execute(
                """CREATE TABLE jobs(
                       task_id TEXT PRIMARY KEY,
                       state TEXT NOT NULL,
                       current_stage TEXT NOT NULL,
                       progress REAL NOT NULL,
                       request_json TEXT NOT NULL,
                       result_json TEXT,
                       retryable INTEGER NOT NULL
                   )"""
            )
            connection.execute(
                """INSERT INTO jobs(task_id, state, current_stage, progress, request_json, result_json, retryable)
                   VALUES (?, 'completed', 'completed', 1.0, '{}', ?, 0)""",
                (task_id, json.dumps({"papers": [], "evidence_papers": evidence_papers})),
            )

        item = next(
            value for value in self.catalog.list_domain_onboardings()
            if value["artifact_id"] == task_id
        )

        self.assertEqual(item["recommendation_count"], 0)

    def test_domain_persistence_keeps_evidence_out_of_recommendations(self) -> None:
        task_id = "domain-persist-evidence"
        self.store.create_domain_onboarding(
            artifact_id=task_id,
            title="领域入门：持久化证据论文",
            query="持久化证据论文",
        )
        self.store.persist_domain_onboarding_result(
            artifact_id=task_id,
            query="持久化证据论文",
            response={
                "status": "ok",
                "papers": [],
                "evidence_papers": [
                    {"paper_id": "persisted-evidence", "title": "Persisted Evidence"}
                ],
            },
        )

        item = next(
            value for value in self.catalog.list_domain_onboardings()
            if value["artifact_id"] == task_id
        )

        self.assertEqual(item["recommendation_count"], 0)

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

    def test_paper_markdown_note_round_trip(self) -> None:
        empty = self.catalog.get_paper_note(self.paper_id)
        saved = self.catalog.set_paper_note(
            self.paper_id,
            "# Core idea\n\n- Verify the robust objective.",
        )

        self.assertEqual(empty["content_markdown"], "")
        self.assertEqual(saved["format"], "markdown")
        self.assertEqual(
            self.catalog.get_paper_note(self.paper_id)["content_markdown"],
            "# Core idea\n\n- Verify the robust objective.",
        )
        self.assertIsNotNone(saved["updated_at"])

    def test_papers_filter_by_whether_precision_reading_exists(self) -> None:
        unread_id = self.store.upsert_paper(
            paper_id="paper-not-reviewed",
            title="Queued Control Paper",
            authors=[],
        )
        self.store.add_to_library(self.paper_id)
        self.store.add_to_library(unread_id)

        reviewed = self.catalog.list_papers(library_only=True, reading_scope="reviewed")
        unreviewed = self.catalog.list_papers(library_only=True, reading_scope="unreviewed")

        self.assertEqual({item["paper_id"] for item in reviewed}, {self.paper_id})
        self.assertEqual({item["paper_id"] for item in unreviewed}, {unread_id})

    def test_folder_and_note_round_trip_without_pipeline_overwrite(self) -> None:
        folder = self.catalog.create_folder("量子控制")
        saved = self.catalog.set_library_item(
            self.paper_id,
            reading_status="unread",
            note="保留这条备注",
            folder_id=folder["folder_id"],
        )
        self.assertTrue(saved)

        self.store.ensure_library_item(self.paper_id, reading_status="reading")
        paper = self.catalog.list_papers(library_only=True)[0]

        self.assertEqual(paper["reading_status"], "reading")
        self.assertEqual(paper["library_note"], "保留这条备注")
        self.assertEqual(paper["folder_name"], "量子控制")
        self.assertNotIn("tags", paper)
        self.assertEqual(self.catalog.list_papers(folder_id=folder["folder_id"])[0]["paper_id"], self.paper_id)

    def test_nested_folders_allow_same_name_in_different_branches(self) -> None:
        project_a = self.catalog.create_folder("项目 A")
        project_b = self.catalog.create_folder("项目 B")
        methods_a = self.catalog.create_folder(
            "方法", parent_folder_id=project_a["folder_id"]
        )
        methods_b = self.catalog.create_folder(
            "方法", parent_folder_id=project_b["folder_id"]
        )
        self.catalog.set_library_item(
            self.paper_id,
            reading_status="unread",
            note="",
            folder_id=methods_a["folder_id"],
        )

        folders = {item["folder_id"]: item for item in self.catalog.list_folders()}
        subtree = self.catalog.list_papers(folder_id=project_a["folder_id"])

        self.assertEqual(folders[methods_a["folder_id"]]["path"], "项目 A / 方法")
        self.assertEqual(folders[methods_b["folder_id"]]["path"], "项目 B / 方法")
        self.assertEqual(subtree[0]["paper_id"], self.paper_id)
        self.assertEqual(subtree[0]["folder_path"], "项目 A / 方法")
        with self.assertRaisesRegex(ValueError, "子文件夹"):
            self.catalog.update_folder(
                project_a["folder_id"],
                name="项目 A",
                parent_folder_id=methods_a["folder_id"],
            )
        with self.assertRaisesRegex(ValueError, "不为空"):
            self.catalog.delete_folder(project_a["folder_id"])


class ResearchLibraryApiTests(unittest.TestCase):
    def test_library_page_is_available(self) -> None:
        response = TestClient(app).get("/library")
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-seefurther-entry="library"', response.text)
        self.assertIn("研究资料库 · 研见 · SeeFurther", response.text)

        legacy_html = (
            Path(__file__).resolve().parents[2] / "gateway/static/library/index.html"
        ).read_text(encoding="utf-8")
        for marker in (
            'id="paper-import"', 'id="paper-file-button"', 'id="reading-filter"',
            'id="paper-note-dialog"', 'id="library-paper-note-content"',
            '/static/vendor/katex/katex.min.js', '/static/paper-reading/note-editor.js',
            'id="folder-tree"', 'id="folder-form-dialog"', 'id="folder-picker-dialog"',
            'href="/app?new=1"',
        ):
            self.assertIn(marker, legacy_html)

        script = (Path(__file__).resolve().parents[2] / "gateway/static/library/app.js").read_text(encoding="utf-8")
        self.assertIn("card.tabIndex = 0", script)
        self.assertIn("function domainWorkspace", script)
        self.assertIn("function conversationUrl", script)
        self.assertNotIn("function conversationWorkspace", script)
        self.assertNotIn("workspace_kind ===", script)
        self.assertNotIn("function openDomainDetail", script)
        self.assertIn("function attachManagedPaper", script)
        self.assertIn("function renderFolderBranch", script)
        self.assertNotIn("window.prompt", script)
        self.assertIn("paper-record-card", script)
        self.assertIn('return item.paper_abstract || "暂无摘要"', script)
        self.assertNotIn('"paper-authors", item.authors.join', script)
        self.assertIn('dataset.action = "view-paper-note"', script)
        self.assertIn("function domainStageLabel", script)
        self.assertIn('paperCount ? `${paperCount} 篇相关论文` : "暂无相关论文"', script)
        self.assertNotIn('阶段：${item.current_stage}', script)
        self.assertIn("function openLibraryPaperNote", script)
        self.assertIn("window.renderPaperMarkdown(note.content_markdown)", script)
        self.assertNotIn('element("pre", "paper-note-source"', script)

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
            empty_note = client.get(f"/api/research/papers/{paper_id}/note")
            saved_note = client.put(
                f"/api/research/papers/{paper_id}/note",
                json={"content_markdown": "# API note\n\nPersistent markdown."},
            )
            loaded_note = client.get(f"/api/research/papers/{paper_id}/note")

            self.assertEqual(library.status_code, 200)
            self.assertEqual(annotation.status_code, 200)
            self.assertEqual(listed.json()[0]["selected_text"], "important result")
            self.assertNotIn("anchor_json", listed.text)
            self.assertEqual(empty_note.json()["content_markdown"], "")
            self.assertEqual(saved_note.status_code, 200)
            self.assertEqual(
                loaded_note.json()["content_markdown"],
                "# API note\n\nPersistent markdown.",
            )

    def test_domain_workspace_restores_persisted_artifact_without_browser_cache(self) -> None:
        with TemporaryDirectory() as directory:
            store = LocalResearchStore(Path(directory) / "research.sqlite3")
            store.initialize()
            task_id = "domain-task-from-library"
            conversation_id = store.create_conversation("量子控制入门会话")
            store.create_domain_onboarding(
                artifact_id=task_id,
                title="领域入门：量子控制",
                query="量子控制入门",
                language="zh-CN",
                conversation_id=conversation_id,
            )
            store.persist_domain_onboarding_result(
                artifact_id=task_id,
                query="量子控制入门",
                response={
                    "status": "ok",
                    "schema_version": "domain-onboarding-output-test",
                    "domain": "量子控制",
                    "text": "从控制目标与脉冲设计开始。",
                    "learning_path": [{"title": "基础"}],
                    "papers": [],
                },
            )
            app.state.research_storage = store
            app.state.domain_onboarding_job_store = None
            app.state.domain_onboarding_job_manager = None

            response = TestClient(app).get(
                f"/api/research/domain-onboardings/{task_id}/workspace"
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["workspace_source"], "catalog")
            self.assertEqual(response.json()["request"]["session_id"], conversation_id)
            self.assertEqual(response.json()["result"]["domain"], "量子控制")
            self.assertEqual(
                response.json()["result"]["learning_path"][0]["title"], "基础"
            )

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
                },
            )
            listed = client.get(
                "/api/research/papers",
                params={"folder_id": folder.json()["folder_id"]},
            )

            self.assertEqual(folder.status_code, 201)
            self.assertEqual(saved.status_code, 200)
            self.assertEqual(listed.json()[0]["folder_name"], "方法论文")
            self.assertNotIn("tags", listed.json()[0])

            moved = client.patch(
                f"/api/research/papers/{paper_id}/folder", json={"folder_id": None}
            )
            after_move = client.get("/api/research/papers").json()[0]
            self.assertEqual(moved.status_code, 200)
            self.assertEqual(after_move["folder_id"], "")
            self.assertEqual(after_move["library_note"], "先看实验")

    def test_start_reading_endpoint_persists_session_and_advances_status(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = LocalResearchStore(root / "research.sqlite3")
            store.initialize()
            paper_id = store.upsert_paper(title="Session Paper", authors=["Author One"])
            paper_storage = PaperReadingStorage(root / "paper_reading", research_store=store)
            paper_storage.save_paper(
                paper_id,
                {"paper_id": paper_id, "title": "Session Paper", "authors": ["Author One"], "sections": []},
            )
            app.state.research_storage = store
            app.state.paper_storage = paper_storage
            app.state.session_manager = SessionManager(storage=paper_storage)

            response = TestClient(app).post(
                f"/api/research/papers/{paper_id}/reading-session"
            )
            reading_session_id = response.json()["reading_session_id"]
            readings = ResearchCatalog(store).list_paper_readings()
            paper = ResearchCatalog(store).list_papers(library_only=True)[0]

            self.assertEqual(response.status_code, 201)
            self.assertEqual(readings[0]["reading_session_id"], reading_session_id)
            self.assertEqual(paper["latest_reading_session_id"], reading_session_id)
            self.assertEqual(paper["reading_status"], "reading")

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

    def test_annotation_accepts_text_anchor_before_pdf_rects_are_resolved(self) -> None:
        with TemporaryDirectory() as directory:
            store = LocalResearchStore(Path(directory) / "research.sqlite3")
            store.initialize()
            paper_id = store.upsert_paper(title="Reflow Anchor", authors=[])
            app.state.research_storage = store

            response = TestClient(app).put(
                f"/api/research/papers/{paper_id}/annotations/mark-reflow",
                json={
                    "annotation_type": "highlight",
                    "page_number": 1,
                    "section_id": "section:2",
                    "selected_text": "shared text anchor",
                    "rects": [],
                },
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["rects"], [])


class FolderSchemaMigrationTests(unittest.TestCase):
    def test_schema_v6_removes_deprecated_tag_tables(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "research.sqlite3"
            store = LocalResearchStore(database)
            store.initialize()
            with store._connection() as connection:
                connection.execute(
                    "CREATE TABLE paper_tags(tag_id TEXT PRIMARY KEY, name TEXT, created_at TEXT)"
                )
                connection.execute(
                    "CREATE TABLE paper_tag_links(paper_id TEXT, tag_id TEXT, added_at TEXT)"
                )

            store.initialize()

            self.assertNotIn("paper_tags", store.list_table_names())
            self.assertNotIn("paper_tag_links", store.list_table_names())

    def test_v4_global_folder_names_migrate_to_sibling_uniqueness(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "research.sqlite3"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    """CREATE TABLE paper_folders (
                       folder_id TEXT PRIMARY KEY,
                       name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                       parent_folder_id TEXT REFERENCES paper_folders(folder_id) ON DELETE SET NULL,
                       created_at TEXT NOT NULL,
                       updated_at TEXT NOT NULL)"""
                )
            store = LocalResearchStore(database)
            store.initialize()
            catalog = ResearchCatalog(store)
            left = catalog.create_folder("左侧")
            right = catalog.create_folder("右侧")

            catalog.create_folder("方法", parent_folder_id=left["folder_id"])
            catalog.create_folder("方法", parent_folder_id=right["folder_id"])

            self.assertEqual(len(catalog.list_folders()), 4)


if __name__ == "__main__":
    unittest.main()
