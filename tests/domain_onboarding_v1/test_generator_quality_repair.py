from __future__ import annotations

import unittest

from handlers.domain_onboarding.config import DomainOnboardingConfig
from handlers.domain_onboarding.generator import StructuredOnboardingGenerator
from handlers.domain_onboarding.quality import CompositeQualityEvaluator
from handlers.domain_onboarding.ranking import WeightedPaperRanker
from handlers.domain_onboarding.repair import TargetedRepairer
from handlers.domain_onboarding.schemas import (
    DomainOnboardingRequest,
    EvidenceClaim,
    QualityIssue,
)

from .fakes import (
    FakeJSONModel,
    make_candidates,
    make_generation_payload,
    make_plan,
    make_profile,
)


class GeneratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = DomainOnboardingConfig()
        self.ranked = WeightedPaperRanker(self.config).rank(
            make_candidates(), make_plan(), limit=6
        ).papers

    def test_generator_uses_only_canonical_candidate_metadata(self) -> None:
        payload = make_generation_payload([paper.paper_id for paper in self.ranked])
        payload["development_stages"][0]["related_paper_ids"].append("invented-paper")
        payload["papers"] = [{"paper_id": "invented-paper", "title": "Fake"}]
        generator = StructuredOnboardingGenerator(FakeJSONModel([payload]), self.config)
        output = generator.generate(
            DomainOnboardingRequest(query="RAG"), make_profile(), make_plan(), self.ranked
        ).output
        allowed = {paper.paper_id for paper in self.ranked}
        self.assertEqual({paper.paper_id for paper in output.papers}, allowed)
        self.assertNotIn("invented-paper", output.development_stages[0].related_paper_ids)
        canonical = {paper.paper_id: paper for paper in self.ranked}
        for paper in output.papers:
            self.assertEqual(paper.title, canonical[paper.paper_id].title)

    def test_learning_path_is_fixed_order_and_profile_sensitive(self) -> None:
        payload = make_generation_payload([paper.paper_id for paper in self.ranked])
        generator = StructuredOnboardingGenerator(FakeJSONModel([payload]), self.config)
        output = generator.generate(
            DomainOnboardingRequest(query="RAG"),
            make_profile("experiment_first"),
            make_plan(),
            self.ranked,
        ).output
        self.assertEqual([step.step for step in output.learning_path], ["1", "2", "3", "4", "5"])
        self.assertTrue(any("复现" in activity for step in output.learning_path[2:] for activity in step.activities))
        self.assertTrue(all(step.completion_criteria for step in output.learning_path))

    def test_evidence_claims_keep_only_allowed_paper_ids(self) -> None:
        payload = make_generation_payload([paper.paper_id for paper in self.ranked])
        payload["evidence_claims"] = [
            {
                "claim": "retrieval augmented generation method benchmark evaluation",
                "supporting_paper_ids": [self.ranked[0].paper_id, "invented-paper"],
                "support_type": "abstract_explicit",
            }
        ]
        output = StructuredOnboardingGenerator(
            FakeJSONModel([payload]), self.config
        ).generate(
            DomainOnboardingRequest(query="RAG"),
            make_profile(),
            make_plan(),
            self.ranked,
        ).output

        self.assertEqual(
            output.evidence_claims[0].supporting_paper_ids,
            [self.ranked[0].paper_id],
        )


class QualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = DomainOnboardingConfig()
        self.ranked = WeightedPaperRanker(self.config).rank(
            make_candidates(), make_plan(), limit=6
        ).papers
        payload = make_generation_payload([paper.paper_id for paper in self.ranked])
        self.output = StructuredOnboardingGenerator(FakeJSONModel([payload]), self.config).generate(
            DomainOnboardingRequest(query="RAG"), make_profile(), make_plan(), self.ranked
        ).output
        self.evaluator = CompositeQualityEvaluator(self.config)

    def test_complete_output_passes_hard_gates(self) -> None:
        quality = self.evaluator.evaluate(self.output, self.ranked)
        self.assertTrue(quality.passed_hard_gates)
        self.assertGreaterEqual(quality.score, quality.threshold)
        self.assertEqual(
            set(quality.dimensions),
            {
                "structure",
                "paper_validity",
                "evidence_grounding",
                "topic_coverage",
                "development_coherence",
                "learning_path",
                "goal_alignment",
            },
        )

    def test_modified_paper_metadata_fails_hard_gate(self) -> None:
        self.output.papers[0].title = "Model invented title"
        quality = self.evaluator.evaluate(self.output, self.ranked)
        self.assertFalse(quality.passed_hard_gates)
        self.assertTrue(any(issue.issue_type == "invalid_paper" for issue in quality.issues))

    def test_content_completeness_cannot_offset_invalid_paper(self) -> None:
        self.output.learning_path[0].paper_ids.append("outside-candidate-set")
        quality = self.evaluator.evaluate(self.output, self.ranked)
        self.assertFalse(quality.passed_hard_gates)
        self.assertEqual(quality.dimensions["paper_validity"], 1.0)

    def test_unsupported_explicit_claim_fails_hard_gate(self) -> None:
        self.output.evidence_claims = [
            EvidenceClaim(
                claim="This paper proves a quantum computing theorem",
                supporting_paper_ids=[self.ranked[0].paper_id],
                support_type="abstract_explicit",
            )
        ]

        quality = self.evaluator.evaluate(self.output, self.ranked)

        self.assertFalse(quality.passed_hard_gates)
        self.assertTrue(
            any(issue.issue_type == "unsupported_claim" for issue in quality.issues)
        )

    def test_evidence_id_outside_candidate_set_fails_hard_gate(self) -> None:
        self.output.evidence_claims[0].supporting_paper_ids.append("invented-paper")

        quality = self.evaluator.evaluate(self.output, self.ranked)

        self.assertFalse(quality.passed_hard_gates)
        self.assertTrue(
            any(
                issue.issue_type == "invalid_paper"
                and issue.target_path.startswith("evidence_claims")
                for issue in quality.issues
            )
        )

    def test_supported_explicit_claim_scores_full_claim_support(self) -> None:
        self.output.evidence_claims = [
            EvidenceClaim(
                claim="retrieval augmented generation method benchmark evaluation",
                supporting_paper_ids=[paper.paper_id for paper in self.ranked[:3]],
                support_type="abstract_explicit",
            )
        ]

        quality = self.evaluator.evaluate(self.output, self.ranked)

        self.assertTrue(quality.passed_hard_gates)
        self.assertGreaterEqual(quality.dimensions["evidence_grounding"], 0.75)

    def test_cross_language_explicit_claim_is_warning_not_hard_failure(self) -> None:
        self.output.evidence_claims = [
            EvidenceClaim(
                claim="该方法通过检索外部证据增强生成结果",
                supporting_paper_ids=[paper.paper_id for paper in self.ranked[:3]],
                support_type="abstract_explicit",
            )
        ]

        quality = self.evaluator.evaluate(self.output, self.ranked)

        self.assertTrue(quality.passed_hard_gates)
        self.assertTrue(
            any(
                issue.issue_type == "unsupported_claim" and issue.severity == "warning"
                for issue in quality.issues
            )
        )


class RepairTests(unittest.TestCase):
    def test_code_repair_removes_invalid_ids_and_repairs_numbering(self) -> None:
        config = DomainOnboardingConfig(max_content_repairs=0)
        ranked = WeightedPaperRanker(config).rank(make_candidates(), make_plan(), limit=6).papers
        payload = make_generation_payload([paper.paper_id for paper in ranked])
        generator = StructuredOnboardingGenerator(FakeJSONModel([payload]), config)
        output = generator.generate(
            DomainOnboardingRequest(query="RAG"), make_profile(), make_plan(), ranked
        ).output
        output.learning_path[0].step = "9"
        output.learning_path[0].paper_ids.append("invalid")
        quality = CompositeQualityEvaluator(config).evaluate(output, ranked)
        repair_result = TargetedRepairer(generator, config).repair(
            DomainOnboardingRequest(query="RAG"),
            make_profile(),
            make_plan(),
            output,
            quality,
            ranked,
        )
        self.assertEqual(repair_result.output.learning_path[0].step, "1")
        self.assertNotIn("invalid", repair_result.output.learning_path[0].paper_ids)

    def test_targeted_repair_prompt_receives_quality_issues(self) -> None:
        config = DomainOnboardingConfig()
        ranked = WeightedPaperRanker(config).rank(make_candidates(), make_plan(), limit=6).papers
        payload = make_generation_payload([paper.paper_id for paper in ranked])
        model = FakeJSONModel([payload, payload])
        generator = StructuredOnboardingGenerator(model, config)
        output = generator.generate(
            DomainOnboardingRequest(query="RAG"), make_profile(), make_plan(), ranked
        ).output
        quality = CompositeQualityEvaluator(config).evaluate(output, ranked).model_copy(
            update={
                "issues": [
                    QualityIssue(
                        issue_type="beginner_mismatch",
                        severity="warning",
                        target_path="learning_path",
                        message="not aligned",
                        recommended_action="rewrite path",
                    )
                ]
            }
        )
        repairer = TargetedRepairer(generator, config)
        repair_result = repairer.repair(
            DomainOnboardingRequest(query="RAG"), make_profile(), make_plan(), output, quality, ranked
        )
        self.assertEqual(repair_result.action, "llm_targeted_repair")
        self.assertIn("repair_issues", model.calls[-1]["messages"][1]["content"])


if __name__ == "__main__":
    unittest.main()
