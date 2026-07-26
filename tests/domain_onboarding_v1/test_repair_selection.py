from __future__ import annotations

import unittest

from handlers.domain_onboarding.repair_selection import RepairSelectionPolicy
from handlers.domain_onboarding.schemas import (
    ContentQuality,
    RepairActionRecord,
    RepairRecord,
)


def make_quality(
    score: float,
    *,
    passed_hard_gates: bool = True,
    evidence: float = 0.8,
) -> ContentQuality:
    return ContentQuality(
        score=score,
        threshold=0.75,
        passed_hard_gates=passed_hard_gates,
        dimensions={
            "structure": 0.8,
            "paper_validity": 1.0,
            "evidence_grounding": evidence,
            "learning_path": 0.8,
        },
    )


class RepairSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = RepairSelectionPolicy(0.05)
        self.record = RepairRecord(triggered=True)

    def test_significant_non_regressing_repair_is_selected(self) -> None:
        decision = self.policy.decide(
            make_quality(0.70),
            make_quality(0.82, evidence=0.85),
            self.record,
        )

        self.assertEqual(decision.decision, "repaired_selected")
        self.assertEqual(decision.selected_attempt, 2)
        self.assertEqual(decision.reasons, ["significant_improvement"])

    def test_small_improvement_retains_initial_result(self) -> None:
        decision = self.policy.decide(
            make_quality(0.70),
            make_quality(0.73),
            self.record,
        )

        self.assertEqual(decision.decision, "initial_retained")
        self.assertIn("improvement_too_small", decision.reasons)

    def test_critical_dimension_regression_is_explicit(self) -> None:
        decision = self.policy.decide(
            make_quality(0.70, evidence=0.9),
            make_quality(0.82, evidence=0.7),
            self.record,
        )

        self.assertEqual(decision.decision, "initial_retained")
        self.assertIn("critical_dimension_regressed", decision.reasons)
        self.assertEqual(decision.dimension_deltas["evidence_grounding"], -0.2)

    def test_failed_action_is_included_in_rejection_reasons(self) -> None:
        record = RepairRecord(
            triggered=True,
            actions=[
                RepairActionRecord(
                    action_id="repair-1",
                    action_type="llm",
                    status="failed",
                )
            ],
        )

        decision = self.policy.decide(
            make_quality(0.70),
            make_quality(0.70),
            record,
        )

        self.assertIn("repair_execution_failed", decision.reasons)


if __name__ == "__main__":
    unittest.main()
