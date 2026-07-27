from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evaluation.domain_onboarding.dataset import load_cases
from evaluation.domain_onboarding.metrics import evaluate_cases
from evaluation.domain_onboarding.runner import run_offline_evaluation


FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "evaluation"
    / "fixtures"
    / "domain_onboarding"
    / "v1"
    / "cases.jsonl"
)


class OfflineEvaluationTests(unittest.TestCase):
    def test_six_fixed_domains_produce_reproducible_baseline(self) -> None:
        cases = load_cases(FIXTURE)
        report = evaluate_cases(cases)

        self.assertEqual(len(cases), 6)
        self.assertEqual(
            {case.domain for case in cases},
            {
                "多模态大模型",
                "多智能体辩论",
                "检索增强生成",
                "图神经网络",
                "扩散模型",
                "大模型幻觉检测",
            },
        )
        self.assertEqual(report.hard_gate_pass_rate, 0.666667)
        self.assertEqual(report.hard_gate_agreement_rate, 1.0)
        self.assertEqual(report.repair_improvement_rate, 0.666667)
        self.assertEqual(report.false_positive_rate, 0.2)
        self.assertEqual(report.annotation_status_counts, {"seed": 6})
        self.assertEqual(
            report.policy_summaries[
                "domain-quality-v1.0.0"
            ].repair_improvement_rate,
            report.repair_improvement_rate,
        )
        self.assertTrue(
            all(value == 0.0 for value in report.dimension_stability_range.values())
        )

    def test_runner_can_write_machine_readable_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            report = run_offline_evaluation(FIXTURE, output=output)
            stored = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(stored["case_count"], report.case_count)
        self.assertEqual(stored["policy_versions"], ["domain-quality-v1.0.0"])

    def test_duplicate_case_ids_are_rejected(self) -> None:
        line = FIXTURE.read_text(encoding="utf-8").splitlines()[0]
        with tempfile.TemporaryDirectory() as directory:
            duplicate = Path(directory) / "duplicate.jsonl"
            duplicate.write_text(f"{line}\n{line}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate evaluation case_id"):
                load_cases(duplicate)


if __name__ == "__main__":
    unittest.main()
