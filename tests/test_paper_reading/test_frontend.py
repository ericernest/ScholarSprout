"""论文精读独立前端的静态契约测试。"""

from __future__ import annotations

import tomllib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from gateway.app import (
    legacy_paper_reading_page,
    paper_reading_page,
    paper_reading_upload_pdf,
)
from handlers.paper_reading.harness.storage import PaperReadingStorage


ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "gateway" / "static"
FRONTEND = STATIC / "paper-reading"


class PaperReadingFrontendTests(unittest.TestCase):
    def test_dedicated_page_route_targets_frontend_entry(self) -> None:
        response = paper_reading_page()

        self.assertEqual(Path(response.path), FRONTEND / "index.html")

    def test_legacy_page_route_returns_to_chat_paper_mode(self) -> None:
        response = legacy_paper_reading_page()

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/app?mode=paper_reading")

    def test_frontend_assets_are_packaged(self) -> None:
        config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        patterns = config["tool"]["setuptools"]["package-data"]["gateway"]

        self.assertIn("static/paper-reading/*", patterns)
        self.assertTrue((FRONTEND / "index.html").is_file())
        self.assertTrue((FRONTEND / "styles.css").is_file())
        self.assertTrue((FRONTEND / "app.js").is_file())

    def test_all_backend_actions_have_frontend_calls(self) -> None:
        javascript = (FRONTEND / "app.js").read_text(encoding="utf-8")
        actions = {
            "search_paper",
            "upload_paper",
            "get_paper_detail",
            "start_reading",
            "pause_reading",
            "resume_reading",
            "fork",
            "merge",
            "load_skill",
            "unload_skill",
            "kg_query",
            "get_session_state",
            "get_progress",
        }

        for action in actions:
            with self.subTest(action=action):
                self.assertIn(f'"{action}"', javascript)

    def test_page_exposes_required_workspace_regions(self) -> None:
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")

        for element_id in (
            "pdf-file-input",
            "url-import-form",
            "paper-search-form",
            "paper-ready-card",
            "paper-outline",
            "structured-reader",
            "pdf-frame",
            "analysis-feed",
            "kg-graph",
            "fork-dialog",
        ):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', html)

    def test_chat_mode_exposes_pdf_or_link_composer(self) -> None:
        html = (STATIC / "chat.html").read_text(encoding="utf-8")
        javascript = (STATIC / "app.js").read_text(encoding="utf-8")

        for element_id in (
            "paper-mode-input",
            "paper-file-input",
            "paper-file-button",
            "paper-url-input",
        ):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', html)
        self.assertIn('action: "upload_paper"', javascript)
        self.assertIn('action: "get_paper_detail"', javascript)
        self.assertIn('window.location.href = "/app/paper-reading"', javascript)
        self.assertNotIn('window.location.href = "/paper-reading"', javascript)
        self.assertIn('paperModeInput.addEventListener("drop"', javascript)

    def test_uploaded_pdf_is_served_inline_for_embedded_reader(self) -> None:
        with TemporaryDirectory() as directory:
            storage = PaperReadingStorage(Path(directory))
            storage.save_upload("paper-1", b"%PDF-1.4\n%%EOF")
            request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(paper_storage=storage)))

            response = paper_reading_upload_pdf("paper-1", request)

        self.assertEqual(response.media_type, "application/pdf")
        self.assertTrue(response.headers["content-disposition"].startswith("inline;"))

    def test_agent_answers_use_markdown_renderer(self) -> None:
        javascript = (FRONTEND / "app.js").read_text(encoding="utf-8")

        self.assertIn("function renderMarkdown(source)", javascript)
        self.assertIn("card.append(header, renderMarkdown(text))", javascript)


if __name__ == "__main__":
    unittest.main()
