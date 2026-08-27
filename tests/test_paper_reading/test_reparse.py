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
    def test_reparse_uses_existing_pdf_and_submits_local_parser(self) -> None:
        with TemporaryDirectory() as directory:
            storage = PaperReadingStorage(Path(directory) / "paper-reading")
            storage.save_paper("paper-1", {"paper_id": "paper-1", "title": "Old paper", "parse_status": "done"})
            storage.save_upload("paper-1", b"%PDF-1.4\nexisting")
            state = SimpleNamespace(paper_storage=storage)

            with patch("handlers.paper_reading.handler._schedule_background_parse") as schedule:
                response = _handle_reparse_paper(
                    PaperReadingRequest(action="reparse_paper", paper_id="paper-1"),
                    state,
                )

            saved = storage.load_paper("paper-1")

        self.assertEqual(response["status"], "ok")
        self.assertIn("本地 PDF 解析", response["data"]["message"])
        self.assertEqual(saved["parse_status"], "parsing")
        schedule.assert_called_once()
        self.assertEqual(schedule.call_args.args[1], "paper-1")
        self.assertEqual(schedule.call_args.args[2], b"%PDF-1.4\nexisting")

if __name__ == "__main__":
    unittest.main()
