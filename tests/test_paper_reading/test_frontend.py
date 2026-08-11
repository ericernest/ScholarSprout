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
        self.assertTrue((FRONTEND / "note-editor.js").is_file())
        self.assertIn("static/vendor/katex/*", patterns)
        self.assertIn("static/vendor/katex/fonts/*", patterns)
        self.assertTrue((STATIC / "vendor" / "katex" / "katex.min.js").is_file())
        self.assertTrue((STATIC / "vendor" / "katex" / "katex.min.css").is_file())

    def test_all_backend_actions_have_frontend_calls(self) -> None:
        javascript = (FRONTEND / "app.js").read_text(encoding="utf-8")
        actions = {
            "search_paper",
            "upload_paper",
            "get_paper_detail",
            "start_reading",
            "fork",
            "merge",
            "get_session_state",
            "regenerate_reading_map",
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
            "reading-map-grid",
            "reading-map-panel",
            "reading-map-kicker",
            "reading-map-title",
            "reading-map-status-copy",
            "research-overview-button",
            "regenerate-reading-map-button",
            "fork-panel",
            "pdf-fit-select",
            "paper-boot",
            "paper-note-button",
            "paper-note-drawer",
            "paper-note-input",
            "paper-note-save-button",
            "paper-note-toolbar",
            "paper-note-mode",
            "paper-note-normal",
        ):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', html)

        self.assertIn('id="paper-intake" class="intake-view" hidden', html)
        self.assertIn('class="paper-reading-body is-booting"', html)
        self.assertNotIn('id="new-paper-button"', html)

    def test_research_overview_is_named_and_reachable_from_workbench_header(self) -> None:
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        javascript = (FRONTEND / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="research-overview-button"', html)
        self.assertIn('id="reading-map-title">研究总览</h2>', html)
        self.assertIn('$("reading-map-panel")?.scrollIntoView', javascript)
        self.assertIn('$("reading-map-title").textContent = isSurvey ? "综述导读地图" : "研究总览"', javascript)
        self.assertIn('"研究总览与智能索引"', javascript)
        self.assertIn('`正在并行生成${taskLabel}，请稍候。`', javascript)
        self.assertIn('title: "研究问题"', javascript)
        self.assertIn('title: "核心方法"', javascript)

    def test_paper_note_drawer_loads_and_saves_markdown(self) -> None:
        javascript = (FRONTEND / "app.js").read_text(encoding="utf-8")
        editor = (FRONTEND / "note-editor.js").read_text(encoding="utf-8")
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        styles = (FRONTEND / "styles.css").read_text(encoding="utf-8")

        self.assertIn("function loadPaperNote", javascript)
        self.assertIn("function savePaperNote", javascript)
        self.assertIn("content_markdown", javascript)
        self.assertIn(".paper-note-drawer.is-open", styles)
        self.assertIn("function applyMarkdownAction", javascript)
        self.assertIn("new window.PaperMarkdownEditor", javascript)
        self.assertIn("class PaperMarkdownEditor", editor)
        self.assertIn("window.renderPaperMarkdown = renderPaperMarkdown", editor)
        self.assertIn("handleShortcut(event)", editor)
        self.assertIn('contenteditable="true"', html)
        self.assertIn('data-paper-note-mode="normal"', html)
        self.assertIn('data-paper-note-mode="source"', html)
        self.assertIn("正常模式", html)
        self.assertIn("源码模式", html)
        self.assertNotIn("预览模式", html)
        self.assertIn('data-markdown-action="table"', html)
        self.assertIn('data-markdown-action="inline_math"', html)
        self.assertIn('data-markdown-action="block_math"', html)
        self.assertIn("window.katex.render", editor)
        self.assertIn("if (/^[0-6]$/.test(event.key)", editor)
        self.assertIn("function syncPaperNoteDrawerBounds", javascript)
        self.assertIn('setPaperNoteMode("normal")', javascript)
        self.assertIn("--paper-note-height", styles)
        self.assertIn("#paper-note-save-button", styles)
        self.assertIn("background: #0b6b57", styles)
        self.assertNotIn(
            "使用 Markdown 记录；笔记归属于论文，在所有精读会话间共享。",
            html,
        )

    def test_paper_note_stays_inside_reader_and_has_resizable_one_third_height(self) -> None:
        javascript = (FRONTEND / "app.js").read_text(encoding="utf-8")
        styles = (FRONTEND / "styles.css").read_text(encoding="utf-8")
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")

        self.assertIn("if (reader && drawer.parentElement !== reader) reader.append(drawer)", javascript)
        self.assertIn("function bindPaperNoteResize", javascript)
        self.assertIn('handle.addEventListener("pointermove"', javascript)
        self.assertIn("paper_reading_note_height_ratio", javascript)
        self.assertIn("height: var(--paper-note-height,33.333%)", styles)
        self.assertIn("position: absolute", styles)
        self.assertIn("cursor: ns-resize", styles)
        self.assertIn("拖动调整论文笔记高度", html)

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
        self.assertIn("card.append(header, renderAgentResponse(text))", javascript)
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
        self.assertIn('id="outline-source-warning"', html)
        self.assertIn("function renderOutlineSourceWarning()", javascript)
        self.assertIn("function readingMapPhaseText()", javascript)
        self.assertIn("extracting_sections", javascript)
        self.assertIn("readingMapProgress", javascript)
        self.assertIn("PDF 未提供内置目录", javascript)
        self.assertIn('warning.classList.add("is-info")', javascript)
        self.assertNotIn("<dialog", html)
        self.assertNotIn(".showModal()", javascript)

    def test_agent_answers_render_latex_and_have_width_controls(self) -> None:
        javascript = (FRONTEND / "app.js").read_text(encoding="utf-8")
        styles = (FRONTEND / "styles.css").read_text(encoding="utf-8")
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")

        self.assertIn("function typesetResponseMath", javascript)
        self.assertIn("window.katex.render", javascript)
        self.assertIn('closest("code,pre,script,style,textarea,.katex")', javascript)
        self.assertIn('id="copilot-narrow-button"', html)
        self.assertIn('id="copilot-wide-button"', html)
        self.assertIn("function setCopilotWidth", javascript)
        self.assertIn(".response-math-block", styles)

    def test_structured_agent_answers_do_not_render_raw_json(self) -> None:
        javascript = (FRONTEND / "app.js").read_text(encoding="utf-8")

        self.assertIn("function parseStructuredAgentResponse", javascript)
        self.assertIn("function renderAgentResponse", javascript)
        self.assertIn("renderAgentResponse(inline.answer", javascript)
        self.assertIn("card.append(header, renderAgentResponse(text))", javascript)
        self.assertIn("structuredValueText(item)", javascript)
        self.assertNotIn('create("pre", "analysis-text", JSON.stringify', javascript)

    def test_low_value_manual_session_controls_are_not_rendered(self) -> None:
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        javascript = (FRONTEND / "app.js").read_text(encoding="utf-8")

        for element_id in ("pause-button", "resume-button", "progress-button"):
            self.assertNotIn(f'id="{element_id}"', html)
        self.assertNotIn('action: "pause_reading"', javascript)
        self.assertIn("function saveBeforeUnload()", javascript)

    def test_agent_chat_shows_questions_and_enter_submits(self) -> None:
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        javascript = (FRONTEND / "app.js").read_text(encoding="utf-8")

        self.assertIn("function appendUserQuestion", javascript)
        self.assertIn('event.key === "Enter" && !event.shiftKey', javascript)
        self.assertIn('$("reading-chat-form").requestSubmit()', javascript)
        self.assertIn("Enter 发送，Shift+Enter 换行", html)

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
        self.assertNotIn("kg_query", javascript)
        self.assertNotIn("知识图谱", html)


if __name__ == "__main__":
    unittest.main()
