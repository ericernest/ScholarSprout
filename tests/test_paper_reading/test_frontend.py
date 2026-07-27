"""论文精读独立前端的静态契约测试。"""

from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

from gateway.app import paper_reading_page


ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "gateway" / "static"
FRONTEND = STATIC / "paper-reading"


class PaperReadingFrontendTests(unittest.TestCase):
    def test_dedicated_page_route_targets_frontend_entry(self) -> None:
        response = paper_reading_page()

        self.assertEqual(Path(response.path), FRONTEND / "index.html")

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

    def test_chat_mode_redirects_to_dedicated_workspace(self) -> None:
        javascript = (STATIC / "app.js").read_text(encoding="utf-8")

        self.assertIn('window.location.href = "/paper-reading"', javascript)


if __name__ == "__main__":
    unittest.main()
