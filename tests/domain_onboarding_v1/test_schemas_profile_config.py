from __future__ import annotations

import unittest
import json
from pathlib import Path

from pydantic import ValidationError

from handlers.domain_onboarding.config import DomainOnboardingConfig
from handlers.domain_onboarding.policy import DomainOnboardingPolicy, PolicyRegistry
from handlers.domain_onboarding.profile import RuleBasedProfileBuilder
from handlers.domain_onboarding.schemas import (
    ContentQuality,
    CurrentLandscape,
    DomainOnboardingRequest,
    DomainOnboardingOutput,
    PaperCandidate,
    PipelineResult,
    Prerequisite,
    QualityAttempt,
    QualityIssue,
    RepairActionRecord,
    RepairDecision,
    RepairRecord,
    stable_id,
)


class ConfigAndSchemaTests(unittest.TestCase):
    def test_v15_complete_example_matches_output_and_audit_contracts(self) -> None:
        root = Path(__file__).resolve().parents[2]
        snapshot = json.loads(
            (root / "docs/examples/domain-onboarding-incremental-response-v1.5.json").read_text(encoding="utf-8")
        )
        raw = snapshot["result"]
        output = DomainOnboardingOutput.model_validate(raw)
        result = PipelineResult(
            status=raw["status"],
            query=raw["query"],
            output=output,
            policy_version=raw["policy_version"],
            policy_fingerprint=raw["policy_fingerprint"],
            quality=raw["quality"],
            quality_attempts=raw["quality_attempts"],
            repair_record=raw["repair_record"],
        )

        self.assertEqual(result.output.schema_version, "domain-onboarding-output-v1.5")
        self.assertEqual(len(result.output.development_stages), 3)
        self.assertEqual(len(result.output.learning_path), 5)

    def test_quality_policy_snapshot_has_stable_version_and_fingerprint(self) -> None:
        config = DomainOnboardingConfig()

        first = config.to_policy()
        second = config.to_policy()

        self.assertEqual(first.policy_version, "domain-quality-v1.5.0")
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(sum(first.dimension_weights.values()), 1.0)
        self.assertGreater(config.planning_timeout_seconds, 60.0)
        self.assertLess(
            config.planning_model_timeout_seconds,
            config.planning_timeout_seconds,
        )

    def test_quality_policy_requires_complete_normalized_weights(self) -> None:
        with self.assertRaises(ValidationError):
            DomainOnboardingPolicy(dimension_weights={"structure": 1.0})

        weights = DomainOnboardingPolicy().dimension_weights.copy()
        weights["structure"] += 0.1
        with self.assertRaises(ValidationError):
            DomainOnboardingPolicy(dimension_weights=weights)

    def test_policy_registry_rejects_reusing_version_for_different_content(self) -> None:
        registry = PolicyRegistry([DomainOnboardingPolicy()])

        with self.assertRaises(ValueError):
            registry.register(DomainOnboardingPolicy(quality_threshold=0.8))

        self.assertEqual(registry.versions(), ["domain-quality-v1.5.0"])

    def test_ranking_weights_must_sum_to_one(self) -> None:
        with self.assertRaises(ValidationError):
            DomainOnboardingConfig(relevance_weight=0.9)

    def test_selected_limit_cannot_exceed_candidates(self) -> None:
        with self.assertRaises(ValidationError):
            DomainOnboardingConfig(candidate_paper_limit=5, selected_paper_limit=6)

    def test_retrieval_max_backoff_cannot_be_smaller_than_base(self) -> None:
        with self.assertRaises(ValidationError):
            DomainOnboardingConfig(
                retrieval_backoff_seconds=2,
                retrieval_max_backoff_seconds=1,
            )

    def test_mmr_settings_are_bounded(self) -> None:
        with self.assertRaises(ValidationError):
            DomainOnboardingConfig(mmr_lambda=1.1)
        with self.assertRaises(ValidationError):
            DomainOnboardingConfig(mmr_role_bonus=0.3)

    def test_request_rejects_empty_query(self) -> None:
        with self.assertRaises(ValidationError):
            DomainOnboardingRequest(query="  ")

    def test_future_graph_ids_are_stable(self) -> None:
        first = Prerequisite(name="线性代数")
        second = Prerequisite(name="线性代数")
        self.assertEqual(first.prerequisite_id, second.prerequisite_id)
        landscape = CurrentLandscape(subdirections=["检索优化"])
        self.assertEqual(landscape.subdirection_ids["检索优化"], stable_id("sub", "检索优化"))

    def test_paper_identifiers_are_normalized(self) -> None:
        paper = PaperCandidate(
            paper_id="paper-1",
            title="Grounded Retrieval",
            url="https://example.org/paper-1",
            source="test",
            doi="https://doi.org/10.1000/ABC",
            arxiv_id="arXiv:2401.00001v2",
            publication_types=["Journal Article", "Journal Article"],
        )
        self.assertEqual(paper.doi, "10.1000/abc")
        self.assertEqual(paper.arxiv_id, "2401.00001")
        self.assertEqual(paper.publication_types, ["Journal Article"])

    def test_paper_rejects_malformed_identifiers(self) -> None:
        with self.assertRaises(ValidationError):
            PaperCandidate(
                paper_id="paper-1",
                title="Grounded Retrieval",
                url="https://example.org/paper-1",
                source="test",
                doi="not-a-doi",
            )

    def test_quality_issue_gets_stable_audit_metadata(self) -> None:
        values = {
            "issue_type": "invalid_paper",
            "severity": "critical",
            "target_path": "papers[0]",
            "message": "paper metadata changed",
            "recommended_action": "restore canonical metadata",
        }
        first = QualityIssue(**values)
        second = QualityIssue(**values)

        self.assertEqual(first.issue_id, second.issue_id)
        self.assertEqual(first.dimension, "paper_validity")
        self.assertTrue(first.hard_gate)
        self.assertEqual(first.repairability, "code")

        evidence_warning = QualityIssue(
            issue_type="unsupported_claim",
            severity="warning",
            target_path="evidence_claims[0]",
            message="cross-language support is uncertain",
            recommended_action="review the claim",
        )
        evidence_error = QualityIssue(
            issue_type="unsupported_claim",
            severity="error",
            target_path="evidence_claims[0]",
            message="claim is unsupported",
            recommended_action="replace the evidence",
        )
        self.assertFalse(evidence_warning.hard_gate)
        self.assertTrue(evidence_error.hard_gate)

    def test_quality_state_is_derived_from_gate_and_threshold(self) -> None:
        passed = ContentQuality(
            score=0.8,
            threshold=0.75,
            passed_hard_gates=True,
            dimensions={},
        )
        warning = passed.model_copy(update={"score": 0.7})
        warning = ContentQuality.model_validate(warning.model_dump())
        failed = ContentQuality(
            score=0.9,
            threshold=0.75,
            passed_hard_gates=False,
            dimensions={},
        )

        self.assertEqual(passed.state, "passed")
        self.assertEqual(warning.state, "warning")
        self.assertEqual(failed.state, "failed")

    def test_quality_and_repair_audit_contract_round_trip(self) -> None:
        quality = ContentQuality(
            policy_version="domain-quality-v1.2.0",
            policy_fingerprint="0123456789abcdef",
            score=0.7,
            threshold=0.75,
            passed_hard_gates=True,
            dimensions={"structure": 1.0},
        )
        attempt = QualityAttempt(
            attempt_number=1,
            source="initial",
            quality=quality,
            duration_ms=12.5,
        )
        action = RepairActionRecord(
            action_id="repair-1",
            action_type="code",
            status="applied",
            issue_ids=["issue-1"],
            target_paths=["learning_path"],
            changed_paths=["learning_path[0].step"],
        )
        record = RepairRecord(
            policy_version="domain-quality-v1.2.0",
            policy_fingerprint="0123456789abcdef",
            triggered=True,
            actions=[action],
            decision=RepairDecision(
                selected_attempt=1,
                decision="initial_retained",
                reasons=["improvement_too_small"],
                score_delta=0.01,
            ),
        )

        self.assertEqual(
            QualityAttempt.model_validate_json(attempt.model_dump_json()),
            attempt,
        )
        self.assertEqual(
            RepairRecord.model_validate_json(record.model_dump_json()),
            record,
        )
        self.assertEqual(attempt.quality.policy_version, record.policy_version)

    def test_pipeline_result_rejects_drifted_selected_quality_attempt(self) -> None:
        quality = ContentQuality(
            policy_fingerprint="0123456789abcdef",
            score=0.8,
            threshold=0.75,
            passed_hard_gates=True,
            dimensions={"structure": 1.0},
        )
        drifted = quality.model_copy(update={"issues": [
            QualityIssue(
                issue_type="unsupported_claim",
                severity="warning",
                target_path="evidence_claims[0]",
                message="drifted",
                recommended_action="repair",
            )
        ]})

        with self.assertRaisesRegex(ValidationError, "selected quality attempt"):
            PipelineResult(
                policy_fingerprint="0123456789abcdef",
                status="ok",
                query="RAG",
                quality=quality,
                quality_attempts=[
                    QualityAttempt(
                        attempt_number=1,
                        source="initial",
                        quality=drifted,
                    )
                ],
            )


class ProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.builder = RuleBasedProfileBuilder()

    def test_defaults_for_domain_only_request(self) -> None:
        profile = self.builder.build(DomainOnboardingRequest(query="图神经网络"))
        self.assertEqual(profile.preference, "balanced")
        self.assertIsNone(profile.time_budget_weeks)
        self.assertTrue(profile.goal)

    def test_metadata_has_priority(self) -> None:
        request = DomainOnboardingRequest(
            query="我想学习 RAG",
            metadata={
                "background": ["机器学习"],
                "goal": "完成课程项目",
                "time_budget_weeks": 8,
                "preference": "theory_first",
            },
        )
        profile = self.builder.build(request)
        self.assertEqual(profile.background, ["机器学习"])
        self.assertEqual(profile.time_budget_weeks, 8)
        self.assertEqual(profile.preference, "theory_first")

    def test_rules_parse_background_time_and_preference(self) -> None:
        request = DomainOnboardingRequest(
            query="我已经学过 Transformer，希望六周完成一个实验，偏向实践"
        )
        profile = self.builder.build(request)
        self.assertIn("Transformer", profile.background[0])
        self.assertEqual(profile.time_budget_weeks, 6)
        self.assertEqual(profile.preference, "experiment_first")


if __name__ == "__main__":
    unittest.main()
