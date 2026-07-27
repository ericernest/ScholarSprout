from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from handlers.domain_onboarding.adaptive_repair import (
    AdaptiveRepairAdvisor,
    AdaptiveRepairConfig,
    AdaptiveRepairPolicy,
    AdaptiveRepairPolicyBuilder,
    load_audit_records,
)
from handlers.domain_onboarding.audit import DomainOnboardingAuditRecord
from handlers.domain_onboarding.schemas import (
    ContentQuality,
    QualityAttempt,
    QualityIssue,
    RepairActionRecord,
    RepairDecision,
    RepairRecord,
)


def make_record(
    *,
    request_id: str,
    action_type: str,
    selected: bool,
    issue_type: str = "route_conflict",
) -> DomainOnboardingAuditRecord:
    issue = QualityIssue(
        issue_type=issue_type,
        severity="warning",
        target_path="learning_path",
        message="test issue",
        recommended_action="repair",
    )
    quality = ContentQuality(
        score=0.6,
        threshold=0.75,
        passed_hard_gates=True,
        dimensions={"learning_path": 0.4},
        issues=[issue],
    )
    decision = RepairDecision(
        selected_attempt=2 if selected else 1,
        decision="repaired_selected" if selected else "initial_retained",
        reasons=["significant_improvement" if selected else "improvement_too_small"],
        score_delta=0.2 if selected else 0.01,
    )
    return DomainOnboardingAuditRecord(
        request_id=request_id,
        recorded_at=datetime(2026, 7, 27, tzinfo=UTC),
        query_hash=f"hash-{request_id}",
        policy_version="domain-quality-v1.0.0",
        status="ok" if selected else "quality_warning",
        total_duration_ms=100.0 if action_type == "code" else 500.0,
        token_usage={"total_tokens": 0 if action_type == "code" else 1000},
        quality_attempts=[QualityAttempt(attempt_number=1, source="initial", quality=quality)],
        repair_record=RepairRecord(
            triggered=True,
            actions=[
                RepairActionRecord(
                    action_id=f"action-{request_id}",
                    action_type=action_type,
                    status="applied",
                    issue_ids=[str(issue.issue_id)],
                )
            ],
            decision=decision,
        ),
    )


class AdaptiveRepairTests(unittest.TestCase):
    def test_builder_prefers_action_with_stronger_conservative_evidence(self) -> None:
        records = [
            make_record(request_id=f"code-{index}", action_type="code", selected=True)
            for index in range(5)
        ] + [
            make_record(request_id=f"llm-{index}", action_type="llm", selected=False)
            for index in range(5)
        ]

        policy = AdaptiveRepairPolicyBuilder(
            AdaptiveRepairConfig(min_samples=3)
        ).build(records)

        strategy = policy.strategies["route_conflict"]
        self.assertEqual(strategy.preferred_action, "code")
        self.assertEqual(strategy.evidence.samples, 5)
        self.assertEqual(strategy.evidence.successes, 5)
        self.assertEqual(policy.source_quality_policy_versions, ["domain-quality-v1.0.0"])

    def test_insufficient_samples_do_not_create_strategy(self) -> None:
        policy = AdaptiveRepairPolicyBuilder(
            AdaptiveRepairConfig(min_samples=2)
        ).build([make_record(request_id="one", action_type="code", selected=True)])
        self.assertEqual(policy.strategies, {})

    def test_advisor_only_returns_matching_issue_recommendations(self) -> None:
        policy = AdaptiveRepairPolicyBuilder(
            AdaptiveRepairConfig(min_samples=1)
        ).build([make_record(request_id="one", action_type="code", selected=True)])
        quality = make_record(
            request_id="quality", action_type="code", selected=True
        ).quality_attempts[0].quality

        recommendations = AdaptiveRepairAdvisor(policy).recommend(quality)

        self.assertEqual(recommendations, {"route_conflict": "code"})

    def test_policy_roundtrip_and_audit_loader(self) -> None:
        record = make_record(request_id="one", action_type="code", selected=True)
        policy = AdaptiveRepairPolicyBuilder(
            AdaptiveRepairConfig(min_samples=1)
        ).build([record])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audit_path = root / "domain-onboarding-2026-07-27.jsonl"
            audit_path.write_text(record.model_dump_json() + "\n", encoding="utf-8")
            policy_path = root / "policy.json"
            policy.save(policy_path)

            self.assertEqual(load_audit_records([root]), [record])
            self.assertEqual(AdaptiveRepairPolicy.load(policy_path), policy)


if __name__ == "__main__":
    unittest.main()
