from __future__ import annotations

import json
import unittest
from pathlib import Path

from handlers.domain_onboarding.repair_selection import RepairSelectionPolicy
from handlers.domain_onboarding.schemas import (
    ContentQuality,
    RepairActionRecord,
    RepairRecord,
)


class QualityRepairCalibrationTests(unittest.TestCase):
    def test_fixed_scenarios_lock_selection_policy(self) -> None:
        fixture = (
            Path(__file__).resolve().parents[2]
            / "docs"
            / "calibration"
            / "domain-onboarding-quality-repair-scenarios.jsonl"
        )
        policy = RepairSelectionPolicy(0.05)

        for line in fixture.read_text(encoding="utf-8").splitlines():
            sample = json.loads(line)
            with self.subTest(scenario=sample["scenario_id"]):
                first = self._quality(sample["first"])
                retry = self._quality(sample["retry"])
                record = RepairRecord(
                    triggered=True,
                    actions=[
                        RepairActionRecord(
                            action_id=f"repair-{index}",
                            action_type=action["action_type"],
                            status=action["status"],
                        )
                        for index, action in enumerate(sample["actions"], start=1)
                    ],
                )

                decision = policy.decide(first, retry, record)

                self.assertEqual(decision.decision, sample["expected_decision"])
                self.assertEqual(decision.reasons, sample["expected_reasons"])

    @staticmethod
    def _quality(values: dict[str, object]) -> ContentQuality:
        return ContentQuality(
            score=values["score"],
            threshold=0.75,
            passed_hard_gates=values["passed_hard_gates"],
            dimensions={
                "structure": 0.8,
                "paper_validity": 1.0,
                "evidence_grounding": values["evidence_grounding"],
                "learning_path": 0.8,
            },
        )


if __name__ == "__main__":
    unittest.main()
