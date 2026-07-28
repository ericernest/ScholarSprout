from __future__ import annotations

import unittest
from pathlib import Path

from evaluation.domain_onboarding.graph_path_evaluation import (
    evaluate_graph_paths,
    load_graph_path_cases,
)


FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "evaluation"
    / "fixtures"
    / "domain_onboarding"
    / "v1"
    / "graph-path-cases.jsonl"
)


class GraphPathEvaluationTests(unittest.TestCase):
    def test_six_domains_keep_graph_in_shadow_mode(self) -> None:
        cases = load_graph_path_cases(FIXTURE)
        report = evaluate_graph_paths(cases)

        self.assertEqual(len(cases), 6)
        self.assertEqual(report.annotation_status_counts, {"seed": 6})
        self.assertEqual(report.graph_validity_rate, 0.833333)
        self.assertEqual(report.fallback_rate, 0.166667)
        self.assertEqual(report.expected_behavior_accuracy, 1.0)
        self.assertEqual(report.dependency_order_rate, 1.0)
        self.assertEqual(report.stage_order_preservation_rate, 1.0)
        self.assertFalse(report.promotion_recommended)

    def test_invalid_baseline_is_rejected(self) -> None:
        payload = load_graph_path_cases(FIXTURE)[0].model_dump(mode="json")
        payload["baseline_order"] = payload["baseline_order"][:-1]
        with self.assertRaisesRegex(ValueError, "baseline_order"):
            type(load_graph_path_cases(FIXTURE)[0]).model_validate(payload)


if __name__ == "__main__":
    unittest.main()
