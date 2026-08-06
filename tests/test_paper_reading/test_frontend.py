"""论文精读独立前端的静态契约测试。"""

from __future__ import annotations

import tomllib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from gateway.app import (
    legacy_paper_reading_page,
    paper_reading_figure,
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
            "fork",
            "merge",
            "kg_query",
            "get_session_state",
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
            "fork-panel",
            "pdf-fit-select",
            "paper-boot",
        ):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', html)

        self.assertIn('id="paper-intake" class="intake-view" hidden', html)
        self.assertIn('class="paper-reading-body is-booting"', html)

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
        self.assertEqual(
            response.headers["cache-control"],
            "private, max-age=31536000, immutable",
        )

    def test_extracted_figure_is_served_inline(self) -> None:
        with TemporaryDirectory() as directory:
            storage = PaperReadingStorage(Path(directory))
            storage.save_figure("paper-1", "figure-1-p1.png", b"\x89PNG\r\n\x1a\n")
            request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(paper_storage=storage)))

            response = paper_reading_figure("paper-1", "figure-1-p1.png", request)

        self.assertEqual(response.media_type, "image/png")
        self.assertTrue(response.headers["content-disposition"].startswith("inline;"))

    def test_agent_answers_use_markdown_renderer(self) -> None:
        javascript = (FRONTEND / "app.js").read_text(encoding="utf-8")
        shared_javascript = (STATIC / "app.js").read_text(encoding="utf-8")
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")

        self.assertIn("function renderMarkdown(source)", javascript)
        self.assertIn("card.append(header, renderMarkdown(text))", javascript)
        self.assertIn("window.renderSafeMarkdown", shared_javascript)
        self.assertIn("isMarkdownTableDivider", shared_javascript)
        self.assertIn('bubble.append(renderSafeMarkdown(content))', shared_javascript)
        self.assertIn("scrollMessageToTop(item)", shared_javascript)
        self.assertIn("scrollAnalysisCardToTop(target, card)", javascript)
        self.assertIn("loadCachedPdfSource", javascript)
        self.assertIn("pdf-page-placeholder", javascript)
        self.assertIn("updateCurrentSectionFromPdfScroll", javascript)
        self.assertIn("function renderPaperFigure(figure)", javascript)
        self.assertIn("figure.image_url", javascript)
        self.assertIn('id="fork-panel"', html)
        self.assertNotIn("<dialog", html)
        self.assertNotIn(".showModal()", javascript)

    def test_low_value_manual_session_controls_are_not_rendered(self) -> None:
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        javascript = (FRONTEND / "app.js").read_text(encoding="utf-8")

        for element_id in ("pause-button", "resume-button", "progress-button"):
            self.assertNotIn(f'id="{element_id}"', html)
        self.assertNotIn('action: "pause_reading"', javascript)
        self.assertIn("function saveBeforeUnload()", javascript)

    def test_annotation_and_pdf_jump_use_workspace_ui(self) -> None:
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        javascript = (FRONTEND / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="note-modal"', html)
        self.assertIn('id="note-text-input"', html)
        self.assertNotIn("window.prompt", javascript)
        self.assertIn("normalizePdfMarkRects", javascript)
        self.assertIn("scrollPageToPdfReader", javascript)
        self.assertIn('window.scrollTo({ top: 0, left: 0, behavior: "auto" })', javascript)
        self.assertIn("document.scrollingElement", javascript)
        self.assertIn("document.fullscreenElement", javascript)
        self.assertIn('jumpToPdfPage(source.page || section?.start_page || 1, source.section_id || "")', javascript)
        self.assertNotIn('id="ready-nodes"', html)
        self.assertNotIn('id="ready-edges"', html)
        self.assertIn("智能索引自动生成", html)


if __name__ == "__main__":
    unittest.main()
