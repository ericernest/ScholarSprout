from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from handlers.paper_reading.handler import _handle_reparse_paper
from handlers.paper_reading.schemas.request import PaperReadingRequest
from storage.paper_reading import PaperReadingStorage


class PaperReparseTests(unittest.TestCase):
    def test_reparse_uses_existing_pdf_and_reports_full_mineru_path(self) -> None:
        with TemporaryDirectory() as directory:
            storage = PaperReadingStorage(Path(directory) / "paper-reading")
            storage.save_paper("paper-1", {"paper_id": "paper-1", "title": "Old paper", "parse_status": "done"})
            storage.save_upload("paper-1", b"%PDF-1.4\nexisting")
            state = SimpleNamespace(
                paper_storage=storage,
                mineru_client=SimpleNamespace(configured=True),
            )

            with patch("handlers.paper_reading.handler._schedule_background_parse") as schedule:
                response = _handle_reparse_paper(
                    PaperReadingRequest(action="reparse_paper", paper_id="paper-1"),
                    state,
                )

            saved = storage.load_paper("paper-1")

        self.assertEqual(response["status"], "ok")
        self.assertTrue(response["data"]["mineru_enabled"])
        self.assertIn("MinerU 完整产物", response["data"]["message"])
        self.assertEqual(saved["parse_status"], "parsing")
        self.assertEqual(saved["mineru_status"], "parsing")
        schedule.assert_called_once()
        self.assertEqual(schedule.call_args.args[1], "paper-1")
        self.assertEqual(schedule.call_args.args[2], b"%PDF-1.4\nexisting")

    def test_mineru_artifacts_are_persisted_separately(self) -> None:
        with TemporaryDirectory() as directory:
            storage = PaperReadingStorage(Path(directory) / "paper-reading")
            saved = storage.save_mineru_artifact("paper-1", "content_list.json", b"[]")

            self.assertEqual(saved.read_bytes(), b"[]")
            self.assertEqual(storage.get_mineru_artifact_path("paper-1", "content_list.json"), saved)
            self.assertEqual(storage.get_storage_stats()["mineru_artifacts"], 1)


if __name__ == "__main__":
    unittest.main()
