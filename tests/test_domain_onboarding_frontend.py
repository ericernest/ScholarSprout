from __future__ import annotations

import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from gateway.app import app


STATIC_DIR = Path(__file__).resolve().parents[1] / "gateway" / "static"


class DomainOnboardingFrontendTests(unittest.TestCase):
    def test_workspace_route_and_assets_exist(self) -> None:
        response = TestClient(app).get("/app/domain-onboarding")
        package_config = (STATIC_DIR.parents[1] / "pyproject.toml").read_text(encoding="utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn("领域学习工作台", response.text)
        self.assertTrue((STATIC_DIR / "domain-onboarding" / "app.js").is_file())
        self.assertTrue((STATIC_DIR / "domain-onboarding" / "styles.css").is_file())
        self.assertIn('"static/domain-onboarding/*"', package_config)

    def test_chat_submits_background_job_and_returns_workspace_card(self) -> None:
        script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn('fetch("/domain_onboarding/jobs"', script)
        self.assertIn("appendDomainOnboardingCard", script)
        self.assertIn("/app/domain-onboarding?task_id=", script)
        self.assertNotIn('initialMode === "paper_reading"', script)

    def test_workspace_consumes_snapshot_sse_and_paper_import(self) -> None:
        script = (STATIC_DIR / "domain-onboarding" / "app.js").read_text(encoding="utf-8")

        self.assertIn("new EventSource", script)
        self.assertIn("/domain_onboarding/jobs/", script)
        self.assertIn("replace_paths", script)
        self.assertIn("return { ...partial, ...result };", script)
        self.assertIn("formatPercentScore(paper.final_score)", script)
        self.assertIn("paperGuidance(paper)", script)
        self.assertIn("sectionStatusCopy", script)
        self.assertIn("生成失败", script)
        self.assertIn("待生成", script)
        self.assertIn('action: "upload_paper"', script)


if __name__ == "__main__":
    unittest.main()
