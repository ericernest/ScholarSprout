"""Regression coverage for creating a paper-reading conversation without an LLM turn."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from handlers.paper_reading.handler import _handle_create_session
from handlers.paper_reading.harness.session import SessionManager
from handlers.paper_reading.harness.storage import PaperReadingStorage
from handlers.paper_reading.schemas.request import PaperReadingRequest
from storage.catalog import ResearchCatalog


class CreatePaperReadingSessionTests(unittest.TestCase):
    def test_create_session_persists_empty_conversation_carrier(self) -> None:
        with TemporaryDirectory() as directory:
            storage = PaperReadingStorage(Path(directory) / "paper_reading")
            storage.save_paper("paper-1", {
                "paper_id": "paper-1",
                "title": "Session Paper",
                "sections": [
                    {"section_id": "sec:1", "title": "Intro", "content": "Body"},
                    {"section_id": "sec:2", "title": "Method", "content": "Body"},
                ],
            })
            state = SimpleNamespace(
                paper_storage=storage,
                session_manager=SessionManager(storage=storage),
            )

            response = _handle_create_session(
                PaperReadingRequest(
                    action="create_session",
                    session_id="",
                    conversation_id="conversation-1",
                    paper_id="paper-1",
                ),
                state,
            )
            conversation = ResearchCatalog(storage.research_store).get_conversation(
                "conversation-1"
            )

        self.assertEqual(response["status"], "ok")
        self.assertNotEqual(response["data"]["session_id"], "conversation-1")
        self.assertEqual(response["data"]["conversation_id"], "conversation-1")
        self.assertEqual(response["progress"]["total_sections"], 2)
        self.assertIsNotNone(conversation)
        self.assertEqual(conversation["reading_session_id"], response["data"]["session_id"])
        self.assertEqual(len(conversation["contexts"]), 1)
        self.assertEqual(conversation["messages"], [])


if __name__ == "__main__":
    unittest.main()
