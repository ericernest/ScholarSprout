from __future__ import annotations

import sqlite3
import time
import unittest
from base64 import b64encode
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from channels.base import ChannelMessage
from gateway.message_flow import process_channel_message
from handlers.domain_onboarding.jobs import DomainOnboardingJobManager, SQLiteJobStore
from handlers.domain_onboarding.metrics import DomainOnboardingMetrics
from handlers.domain_onboarding.schemas import DomainOnboardingRequest, PipelineResult
from handlers.domain_onboarding_handler import handle_domain_onboarding_message
from handlers.paper_reading.handler import _handle_upload_paper
from handlers.paper_reading.schemas import PaperReadingRequest
from storage import LocalResearchStore, PaperReadingStorage, ResearchCatalog


class _Channel:
    def publish_inbound(self, message):
        return None

    def send_outbound(self, message):
        return None


class _Pipeline:
    config = SimpleNamespace(request_timeout_seconds=5.0)

    def run(self, request, trace, execution_context=None, progress_callback=None):
        if progress_callback is not None:
            progress_callback("planning", 0.2, True, [], {})
        return PipelineResult(status="ok", query=request.query)


class RuntimeStorageIntegrationTests(unittest.TestCase):
    def test_message_flow_persists_chat_exchange(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "research.sqlite3"
            store = LocalResearchStore(database)
            store.initialize()
            state = SimpleNamespace(research_storage=store)
            inbound = ChannelMessage(
                session_id="chat-session",
                channel="web",
                direction="inbound",
                mode="chat",
                content="今天讨论数据库",
            )

            process_channel_message(
                _Channel(), inbound, lambda message, app_state: {"text": "好的。"}, state
            )

            with closing(sqlite3.connect(database)) as connection:
                rows = connection.execute(
                    "SELECT role, mode, content FROM messages ORDER BY sequence_number"
                ).fetchall()
            self.assertEqual(rows, [("user", "chat", "今天讨论数据库"), ("assistant", "chat", "好的。")])

    def test_paper_message_does_not_persist_uploaded_base64(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "research.sqlite3"
            store = LocalResearchStore(database)
            store.initialize()
            state = SimpleNamespace(research_storage=store)
            inbound = ChannelMessage(
                session_id="paper-session",
                channel="web",
                direction="inbound",
                mode="paper_reading",
                content={"action": "upload_paper", "pdf_data": "SECRET_BASE64"},
            )

            process_channel_message(
                _Channel(), inbound, lambda message, app_state: {"status": "ok", "action": "upload_paper"}, state
            )

            with closing(sqlite3.connect(database)) as connection:
                content = "\n".join(
                    row[0] for row in connection.execute("SELECT content FROM messages").fetchall()
                )
            self.assertNotIn("SECRET_BASE64", content)
            self.assertIn("upload_paper", content)

    def test_domain_job_updates_shared_artifact_and_conversation(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "research.sqlite3"
            store = LocalResearchStore(database)
            store.initialize()
            job_store = SQLiteJobStore(database)
            manager = DomainOnboardingJobManager(
                _Pipeline(), job_store, max_workers=1, result_store=store
            )
            try:
                job = manager.submit(
                    DomainOnboardingRequest(query="RAG 入门", session_id="domain-session")
                )
                for _ in range(100):
                    snapshot = job_store.get(job["task_id"])
                    if snapshot and snapshot["state"] == "completed":
                        break
                    time.sleep(0.01)
                with closing(sqlite3.connect(database)) as connection:
                    artifact = connection.execute(
                        "SELECT state FROM work_artifacts WHERE artifact_id = ?", (job["task_id"],)
                    ).fetchone()
                    message_count = connection.execute(
                        "SELECT COUNT(*) FROM messages WHERE conversation_id = 'domain-session'"
                    ).fetchone()[0]
                self.assertEqual(artifact[0], "completed")
                self.assertEqual(message_count, 2)
            finally:
                manager.close()

    def test_synchronous_domain_handler_persists_result(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "research.sqlite3"
            store = LocalResearchStore(database)
            store.initialize()
            state = SimpleNamespace(
                research_storage=store,
                domain_onboarding_pipeline=_Pipeline(),
                domain_onboarding_metrics=DomainOnboardingMetrics(),
                domain_onboarding_audit_sink=None,
            )
            message = ChannelMessage(
                session_id="sync-domain-session",
                channel="web",
                direction="inbound",
                mode="domain_onboarding",
                content="符号回归入门",
            )

            response = handle_domain_onboarding_message(message, state)

            self.assertEqual(response["status"], "ok")
            with closing(sqlite3.connect(database)) as connection:
                artifact_count = connection.execute(
                    """SELECT COUNT(*) FROM conversation_artifacts
                       WHERE conversation_id = 'sync-domain-session'"""
                ).fetchone()[0]
            self.assertEqual(artifact_count, 1)

    def test_paper_adapter_writes_document_and_binary_index(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = LocalResearchStore(root / "research.sqlite3")
            store.initialize()
            paper_storage = PaperReadingStorage(root / "paper_reading", store)
            paper_storage.save_upload("paper-1", b"%PDF-test")
            paper_storage.save_paper(
                "paper-1",
                {"paper_id": "paper-1", "title": "Test Paper", "authors": ["A"], "sections": []},
            )

            self.assertEqual(paper_storage.load_paper("paper-1")["title"], "Test Paper")
            with closing(sqlite3.connect(store.database_path)) as connection:
                file_count = connection.execute(
                    "SELECT COUNT(*) FROM paper_files WHERE paper_id = 'paper-1'"
                ).fetchone()[0]
            self.assertEqual(file_count, 1)

    def test_upload_attaches_to_existing_paper_and_enters_library(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = LocalResearchStore(root / "research.sqlite3")
            store.initialize()
            paper_id = store.upsert_paper(
                paper_id="recommended-paper", title="Recommended Paper", authors=[]
            )
            paper_storage = PaperReadingStorage(root / "paper_reading", store)
            state = SimpleNamespace(paper_pipeline=object(), paper_storage=paper_storage)
            request = PaperReadingRequest(
                action="upload_paper",
                paper_id=paper_id,
                pdf_data=b64encode(b"%PDF-1.4\n%%EOF").decode("ascii"),
                metadata={"original_filename": "recommended.pdf"},
            )

            with patch("handlers.paper_reading.handler._schedule_background_parse"):
                response = _handle_upload_paper(request, state)

            self.assertEqual(response["data"]["paper_id"], paper_id)
            library = ResearchCatalog(store).list_papers(library_only=True)
            self.assertEqual(library[0]["paper_id"], paper_id)
            self.assertEqual(library[0]["reading_status"], "unread")


if __name__ == "__main__":
    unittest.main()
