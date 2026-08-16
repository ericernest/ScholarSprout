from __future__ import annotations

import unittest

from handlers.domain_onboarding.config import DomainOnboardingConfig
from handlers.domain_onboarding.generator import StructuredOnboardingGenerator
from handlers.domain_onboarding.quality import CompositeQualityEvaluator
from handlers.domain_onboarding.ranking import WeightedPaperRanker
from handlers.domain_onboarding.repair_code import CodeRepairExecutor
from handlers.domain_onboarding.schemas import (
    DomainOnboardingOutput,
    DomainOnboardingRequest,
)

from .fakes import (
    FakeJSONModel,
    make_candidates,
    make_generation_payload,
    make_plan,
    make_profile,
)


class LearningPaperBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = DomainOnboardingConfig()
        self.ranked = WeightedPaperRanker(self.config).rank(
            make_candidates(), make_plan(), limit=6
        ).papers
        payload = make_generation_payload(
            [paper.paper_id for paper in self.ranked]
        )
        self.output = StructuredOnboardingGenerator(
            FakeJSONModel([payload]), self.config
        ).generate(
            DomainOnboardingRequest(query="RAG"),
            make_profile(),
            make_plan(),
            self.ranked,
        ).output

    def test_learning_tasks_receive_explicit_paper_purposes(self) -> None:
        uses = [
            {binding.learning_use for binding in step.paper_bindings}
            for step in self.output.learning_path
        ]

        self.assertEqual(uses[0], {"concept_introduction"})
        self.assertEqual(uses[1], {"architecture_reference"})
        self.assertEqual(uses[2], {"method_extension"})
        self.assertIn("baseline_implementation", uses[3])
        self.assertTrue(
            uses[3] & {"benchmark_dataset", "evaluation_framework"}
        )
        self.assertEqual(uses[4], {"frontier_problem"})

    def test_evaluation_is_companion_not_baseline_and_application_is_not_frontier(self) -> None:
        roles = {paper.paper_id: paper.paper_role for paper in self.output.papers}
        experiment = self.output.learning_path[3]
        baseline_ids = {
            binding.paper_id
            for binding in experiment.paper_bindings
            if binding.learning_use == "baseline_implementation"
        }
        frontier_ids = {
            binding.paper_id
            for binding in self.output.learning_path[4].paper_bindings
            if binding.learning_use == "frontier_problem"
        }

        self.assertTrue(baseline_ids)
        self.assertTrue(
            all(roles[paper_id] in {"foundational", "method"} for paper_id in baseline_ids)
        )
        self.assertTrue(frontier_ids)
        self.assertTrue(
            all(roles[paper_id] not in {"application", "evaluation"} for paper_id in frontier_ids)
        )

    def test_bindings_are_explainable_compatible_and_navigable(self) -> None:
        for step in self.output.learning_path:
            self.assertEqual(
                [binding.paper_id for binding in step.paper_bindings],
                step.paper_ids,
            )
            self.assertTrue(all(binding.reason for binding in step.paper_bindings))
            self.assertTrue(all(binding.matched_signals for binding in step.paper_bindings))
            self.assertTrue(step.learning_step_id)
        self.assertTrue(
            any(step.related_stage_ids for step in self.output.learning_path)
        )
        self.assertTrue(
            any(step.related_problem_ids for step in self.output.learning_path)
        )
        self.assertTrue(
            any(step.related_subdirection_ids for step in self.output.learning_path)
        )

    def test_binding_contract_round_trip_and_quality_monitoring(self) -> None:
        restored = DomainOnboardingOutput.model_validate_json(
            self.output.model_dump_json()
        )
        self.assertEqual(restored, self.output)
        self.assertEqual(restored.schema_version, "domain-onboarding-output-v1.10")

        experiment = restored.learning_path[3]
        evaluation = next(
            binding
            for binding in experiment.paper_bindings
            if binding.learning_use in {"benchmark_dataset", "evaluation_framework"}
        )
        experiment.paper_bindings = [evaluation]
        experiment.paper_ids = [evaluation.paper_id]
        experiment.papers = [
            paper for paper in experiment.papers if paper.paper_id == evaluation.paper_id
        ]

        quality = CompositeQualityEvaluator(self.config).evaluate(
            restored, self.ranked
        )

        self.assertLess(quality.dimensions["learning_path"], 1.0)
        self.assertTrue(
            any(issue.issue_type == "route_conflict" for issue in quality.issues)
        )

    def test_english_request_gets_english_binding_reasons(self) -> None:
        payload = make_generation_payload(
            [paper.paper_id for paper in self.ranked]
        )
        output = StructuredOnboardingGenerator(
            FakeJSONModel([payload]), self.config
        ).generate(
            DomainOnboardingRequest(query="Learn RAG", language="en-US"),
            make_profile(),
            make_plan(),
            self.ranked,
        ).output

        self.assertTrue(
            all(
                binding.reason.startswith("Use this")
                for step in output.learning_path
                for binding in step.paper_bindings
            )
        )

    def test_code_repair_removes_invalid_binding_with_invalid_paper(self) -> None:
        step = self.output.learning_path[0]
        step.paper_ids.append("invented-paper")
        step.paper_bindings.append(
            step.paper_bindings[0].model_copy(
                update={"paper_id": "invented-paper"}
            )
        )

        repaired = CodeRepairExecutor().execute(self.output, self.ranked)

        repaired_step = repaired.learning_path[0]
        self.assertNotIn("invented-paper", repaired_step.paper_ids)
        self.assertNotIn(
            "invented-paper",
            [binding.paper_id for binding in repaired_step.paper_bindings],
        )


if __name__ == "__main__":
    unittest.main()
