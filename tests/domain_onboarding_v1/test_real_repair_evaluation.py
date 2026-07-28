from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from evaluation.domain_onboarding.repair_evaluation import (
    evaluate_repairs,
    load_repair_cases,
)


FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "evaluation"
    / "fixtures"
    / "domain_onboarding"
    / "v1"
    / "repair-cases.jsonl"
)


class RealRepairEvaluationTests(unittest.TestCase):
    def test_six_domain_repairs_replay_action_and_selection_policy(self) -> None:
        cases = load_repair_cases(FIXTURE)
        report = evaluate_repairs(cases)

        self.assertEqual(len(cases), 6)
        self.assertEqual(len({case.domain for case in cases}), 6)
        self.assertEqual(report.annotation_status_counts, {"seed": 6})
        self.assertEqual(report.action_accuracy, 1.0)
        self.assertEqual(report.selection_accuracy, 1.0)
        self.assertEqual(report.repair_improvement_rate, 0.833333)
        self.assertEqual(report.hard_gate_recovery_rate, 1.0)
        self.assertEqual(report.failed_case_ids, [])

    def test_duplicate_case_ids_are_rejected(self) -> None:
        line = FIXTURE.read_text(encoding="utf-8").splitlines()[0]
        with tempfile.TemporaryDirectory() as directory:
            duplicate = Path(directory) / "duplicate.jsonl"
            duplicate.write_text(f"{line}\n{line}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate repair case_id"):
                load_repair_cases(duplicate)


if __name__ == "__main__":
    unittest.main()
