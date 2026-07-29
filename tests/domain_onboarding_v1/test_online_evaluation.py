from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from evaluation.domain_onboarding.online import (
    OnlineRunLimits,
    load_online_cases,
    run_online_evaluation,
    validate_online_permission,
)
from handlers.domain_onboarding.schemas import (
    ContentQuality,
    CurrentLandscape,
    DomainOnboardingOutput,
    QualityAttempt,
    LearnerProfile,
    PipelineResult,
    QualityIssue,
    SelectedPaper,
)
from runtime.agent_runner import TokenUsage


FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "evaluation"
    / "fixtures"
    / "domain_onboarding"
    / "v1"
    / "online-cases.jsonl"
)


class FakeOnlinePipeline:
    def run(self, request, trace):
        trace.first_model_calls = 1
        trace.first_usage = TokenUsage(100, 50, 150, True)
        trace.planning_duration_ms = 1234.5
        quality = ContentQuality(
            score=0.8,
            threshold=0.75,
            passed_hard_gates=True,
            dimensions={"structure": 0.8},
            issues=[
                QualityIssue(
                    issue_type="unsupported_claim",
                    severity="warning",
                    target_path="evidence_claims[0]",
                    message="cross-language lexical evidence is uncertain",
                    recommended_action="human review",
                )
            ],
        )
        output = DomainOnboardingOutput(
            domain=request.query,
            text="A sufficiently long deterministic online test output summary.",
            learner_profile=LearnerProfile(),
            prerequisites=[],
            development_stages=[],
            current_landscape=CurrentLandscape(),
            learning_path=[],
            papers=[
                SelectedPaper(
                    paper_id="valid-paper",
                    title="Valid Paper",
                    year=2024,
                    url="https://example.org/valid",
                    source="test",
                ),
                SelectedPaper(
                    paper_id="missing-year",
                    title="Missing Year",
                    url="https://example.org/missing-year",
                    source="test",
                ),
            ],
        )
        return PipelineResult(
            status="ok",
            query=request.query,
            output=output,
            quality=quality,
            quality_attempts=[
                QualityAttempt(attempt_number=1, source="initial", quality=quality)
            ],
        )


class OnlineEvaluationTests(unittest.TestCase):
    def test_online_dataset_contains_bilingual_pairs_for_six_domains(self) -> None:
        cases = load_online_cases(FIXTURE)

        self.assertEqual(len(cases), 12)
        by_domain = {}
        for case in cases:
            by_domain.setdefault(case.domain, set()).add(case.language)
        self.assertEqual(len(by_domain), 6)
        self.assertTrue(all(languages == {"zh", "en"} for languages in by_domain.values()))

    def test_permission_requires_environment_confirmation_and_pricing(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "RUN_DOMAIN_ONBOARDING_ONLINE"):
                validate_online_permission(
                    confirmed=True,
                    input_cost_per_million_tokens=1.0,
                    output_cost_per_million_tokens=2.0,
                )
        with patch.dict(os.environ, {"RUN_DOMAIN_ONBOARDING_ONLINE": "1"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "confirm-online"):
                validate_online_permission(
                    confirmed=False,
                    input_cost_per_million_tokens=1.0,
                    output_cost_per_million_tokens=2.0,
                )
            with self.assertRaisesRegex(RuntimeError, "pricing"):
                validate_online_permission(
                    confirmed=True,
                    input_cost_per_million_tokens=None,
                    output_cost_per_million_tokens=None,
                )

    def test_runner_enforces_case_and_cost_limits_and_reports_quality(self) -> None:
        report = run_online_evaluation(
            FakeOnlinePipeline(),
            load_online_cases(FIXTURE),
            OnlineRunLimits(
                max_cases=4,
                max_estimated_cost_usd=0.0001,
                cost_reserve_per_case_usd=0.0001,
            ),
            input_cost_per_million_tokens=1.0,
            output_cost_per_million_tokens=2.0,
        )

        self.assertEqual(report.requested_case_count, 4)
        self.assertEqual(report.completed_case_count, 1)
        self.assertTrue(report.budget_exhausted)
        self.assertEqual(report.paper_validity_rate, 0.5)
        self.assertEqual(report.hard_gate_pass_rate, 1.0)
        self.assertEqual(report.total_tokens, 150)
        self.assertEqual(report.estimated_cost_usd, 0.0002)
        self.assertEqual(report.cross_language_warning_rate, 1.0)
        self.assertEqual(report.cases[0].stage_durations_ms, {"planning": 1234.5})
        self.assertIsNone(report.cases[0].interrupted_stage)
        self.assertEqual(report.cases[0].quality.score, 0.8)
        self.assertEqual(
            report.cases[0].quality.issues[0].target_path,
            "evidence_claims[0]",
        )
        self.assertEqual(len(report.cases[0].quality_attempts), 1)
        self.assertIsNone(report.cases[0].repair_record)


if __name__ == "__main__":
    unittest.main()
