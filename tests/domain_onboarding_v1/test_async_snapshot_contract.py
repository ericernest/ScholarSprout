from __future__ import annotations

import json
import unittest
from pathlib import Path

from evaluation.domain_onboarding.snapshot_validation import (
    validate_completed_snapshot,
)
ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = (
    ROOT / "docs" / "examples" / "domain-onboarding-incremental-response-v1.5.json"
)
EVENTS = (
    ROOT / "docs" / "examples" / "domain-onboarding-incremental-events-v1.5.jsonl"
)


class AsyncSnapshotContractTests(unittest.TestCase):
    def test_checked_in_snapshot_is_real_and_reproducible(self) -> None:
        snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))

        errors = validate_completed_snapshot(
            snapshot,
        )

        self.assertEqual(errors, [])
        self.assertEqual(snapshot["result"]["status"], "quality_warning")
        self.assertEqual(
            [
                issue["issue_type"]
                for issue in snapshot["result"]["quality"]["issues"]
            ],
            ["generation_fallback"],
        )
        self.assertTrue(snapshot["result"]["quality"]["hard_gates"])
        self.assertTrue(
            all(paper.get("abstract") for paper in snapshot["result"]["papers"])
        )

    def test_checked_in_event_log_is_complete_and_contains_no_access_token(self) -> None:
        events = [
            json.loads(line)
            for line in EVENTS.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

        self.assertEqual(
            [event["event"] for event in events],
            [
                "accepted",
                "profile_ready",
                "plan_ready",
                "papers_ready",
                "development_ready",
                "landscape_ready",
                "learning_path_ready",
                "quality_ready",
                "completed",
            ],
        )
        self.assertIn("result", events[-1]["data"])
        self.assertNotIn("access_token", EVENTS.read_text(encoding="utf-8"))

    def test_placeholder_values_are_rejected(self) -> None:
        snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        snapshot["result"]["papers"][0]["abstract"] = "..."

        errors = validate_completed_snapshot(snapshot)

        self.assertTrue(any("placeholder value" in error for error in errors))

    def test_hard_gate_failure_is_not_publishable(self) -> None:
        snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        snapshot["result"]["quality"]["passed_hard_gates"] = False

        errors = validate_completed_snapshot(snapshot)

        self.assertIn(
            "publishable snapshot must pass every quality hard gate", errors
        )


if __name__ == "__main__":
    unittest.main()
