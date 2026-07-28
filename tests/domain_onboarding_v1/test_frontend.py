from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

from gateway.app import (
    domain_onboarding_page,
    legacy_domain_onboarding_page,
)

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "gateway" / "static"
FRONTEND = STATIC / "domain-onboarding"


class DomainOnboardingFrontendTests(unittest.TestCase):
    def test_dedicated_page_route_targets_frontend_entry(self) -> None:
        response = domain_onboarding_page()

        self.assertEqual(Path(response.path), FRONTEND / "index.html")

    def test_legacy_page_route_returns_to_chat_domain_mode(self) -> None:
        response = legacy_domain_onboarding_page()

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/app?mode=domain_onboarding")

    def test_frontend_assets_are_packaged(self) -> None:
        config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        patterns = config["tool"]["setuptools"]["package-data"]["gateway"]

        self.assertIn("static/domain-onboarding/*", patterns)
        for filename in ("index.html", "styles.css", "app.js"):
            with self.subTest(filename=filename):
                self.assertTrue((FRONTEND / filename).is_file())

    def test_page_exposes_learning_workspace_regions(self) -> None:
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")

        for element_id in (
            "onboarding-form",
            "profile-background",
            "loading-view",
            "workbench-view",
            "section-navigation",
            "prerequisite-list",
            "stage-timeline",
            "learning-path",
            "paper-list",
            "evidence-list",
            "quality-content",
        ):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', html)

    def test_frontend_maps_structured_contract_and_paper_handoff(self) -> None:
        javascript = (FRONTEND / "app.js").read_text(encoding="utf-8")

        for field in (
            "learner_profile",
            "prerequisites",
            "development_stages",
            "current_landscape",
            "learning_path",
            "papers",
            "evidence_claims",
            "quality_attempts",
            "repair_record",
        ):
            with self.subTest(field=field):
                self.assertIn(field, javascript)
        self.assertIn('const STORAGE_KEY = "domain_onboarding_workspace_v1_3"', javascript)
        self.assertIn('action: "upload_paper"', javascript)
        self.assertIn('window.location.href = "/app/paper-reading"', javascript)

    def test_chat_domain_mode_navigates_with_query(self) -> None:
        javascript = (STATIC / "app.js").read_text(encoding="utf-8")

        self.assertIn('currentMode === "domain_onboarding"', javascript)
        self.assertIn("/app/domain-onboarding?query=", javascript)


if __name__ == "__main__":
    unittest.main()
