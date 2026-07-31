from __future__ import annotations

import json
import unittest

from handlers.domain_onboarding.config import DomainOnboardingConfig
from handlers.domain_onboarding.generator import GenerationError, StructuredOnboardingGenerator
from handlers.domain_onboarding.quality import CompositeQualityEvaluator
from handlers.domain_onboarding.ranking import WeightedPaperRanker
from handlers.domain_onboarding.repair import TargetedRepairer
from handlers.domain_onboarding.repair_planning import RepairPlanner
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
    def test_incremental_generation_emits_validated_sections_in_display_order(self) -> None:
        config = DomainOnboardingConfig()
        ranked = WeightedPaperRanker(config).rank(make_candidates(), make_plan(), limit=6).papers
        payload = make_generation_payload([paper.paper_id for paper in ranked])
        model = FakeJSONModel([payload, payload, payload])
        events = []

        result = StructuredOnboardingGenerator(model, config).generate_incrementally(
            DomainOnboardingRequest(query="检索增强生成"),
            make_profile(),
            make_plan(),
            ranked,
            lambda event, data, paths: events.append((event, data, paths)),
        )

        self.assertEqual(
            [item[0] for item in events],
            ["development_ready", "landscape_ready", "learning_path_ready"],
        )
        self.assertEqual(result.stats.model_calls, 3)
        self.assertEqual(result.output.language, "zh-CN")
        self.assertTrue(result.output.learning_path[3].reproducibility_checklist)

    def test_incremental_generation_reports_failure_when_json_is_invalid(self) -> None:
        config = DomainOnboardingConfig()
        ranked = WeightedPaperRanker(config).rank(
            make_candidates(), make_plan(), limit=6
        ).papers
        model = FakeJSONModel(["not json", "not json", "not json"])
        events = []

        with self.assertRaisesRegex(
            GenerationError, "LLM did not return a JSON object"
        ):
            StructuredOnboardingGenerator(model, config).generate_incrementally(
                DomainOnboardingRequest(query="RAG"),
                make_profile(),
                make_plan(),
                ranked,
                lambda event, data, paths: events.append((event, data, paths)),
            )

        self.assertEqual(events, [])
        self.assertEqual(len(model.calls), 1)

    def test_incremental_generation_identifies_development_failure(self) -> None:
        config = DomainOnboardingConfig()
        ranked = WeightedPaperRanker(config).rank(
            make_candidates(), make_plan(), limit=6
        ).papers

        with self.assertRaisesRegex(
            GenerationError,
            "development section generation failed: LLM did not return a JSON object",
        ):
            StructuredOnboardingGenerator(
                FakeJSONModel(["not json"]), config
            ).generate_incrementally(
                DomainOnboardingRequest(query="RAG"),
                make_profile(),
                make_plan(),
                ranked,
                lambda *_: None,
            )

    def test_incremental_generation_restores_planner_owned_domain(self) -> None:
        config = DomainOnboardingConfig()
        ranked = WeightedPaperRanker(config).rank(
            make_candidates(), make_plan(), limit=6
        ).papers
        payload = make_generation_payload([paper.paper_id for paper in ranked])
        development = dict(payload)
        development.pop("domain")
        model = FakeJSONModel([development, payload, payload])

        result = StructuredOnboardingGenerator(model, config).generate_incrementally(
            DomainOnboardingRequest(query="RAG"),
            make_profile(),
            make_plan(),
            ranked,
            lambda *_: None,
        )

        self.assertIn(make_plan().normalized_domain, result.output.domain)

    def test_section_payload_omits_retrieval_and_unrelated_completed_context(self) -> None:
        config = DomainOnboardingConfig()
        ranked = WeightedPaperRanker(config).rank(
            make_candidates(), make_plan(), limit=6
        ).papers
        generator = StructuredOnboardingGenerator(FakeJSONModel([]), config)
        request = DomainOnboardingRequest(
            query="RAG",
            metadata={"private_client_hint": "must-not-reach-generation"},
        )
        completed = {
            "domain": "RAG",
            "prerequisites": [{"name": "IR"}],
            "development_stages": [{"stage_id": "stage-1"}],
            "paper_guidance": [{"paper_id": "paper-1"}],
            "evidence_claims": [{"claim": "claim"}],
        }

        development = generator._section_user_payload(
            "development", request, make_profile(), make_plan(), ranked, completed
        )
        landscape = generator._section_user_payload(
            "landscape", request, make_profile(), make_plan(), ranked, completed
        )

        self.assertNotIn("metadata", development["request"])
        self.assertNotIn("search_queries", development["research_plan"])
        self.assertEqual(development["completed_sections"], {})
        self.assertEqual(
            set(landscape["completed_sections"]),
            {"domain", "development_stages"},
        )

    def test_incremental_generation_unwraps_named_section_payloads(self) -> None:
        config = DomainOnboardingConfig()
        ranked = WeightedPaperRanker(config).rank(
            make_candidates(), make_plan(), limit=6
        ).papers
        development = make_generation_payload([paper.paper_id for paper in ranked])
        model = FakeJSONModel(
            [
                {"development": development},
                {"landscape": {"current_landscape": development["current_landscape"]}},
                {"learning_path": {"learning_path": development["learning_path"]}},
            ]
        )

        result = StructuredOnboardingGenerator(model, config).generate_incrementally(
            DomainOnboardingRequest(query="RAG"),
            make_profile(),
            make_plan(),
            ranked,
            lambda *_: None,
        )

        self.assertEqual(len(result.output.prerequisites), 3)
        self.assertEqual(len(result.output.development_stages), 3)
        self.assertEqual(len(result.output.learning_path), 5)
        self.assertTrue(result.output.papers[0].contribution)
        self.assertTrue(result.output.papers[0].reading_focus)

    def test_incremental_generation_merges_outer_and_wrapped_section_fields(self) -> None:
        config = DomainOnboardingConfig()
        ranked = WeightedPaperRanker(config).rank(
            make_candidates(), make_plan(), limit=6
        ).papers
        payload = make_generation_payload([paper.paper_id for paper in ranked])
        development = {
            "domain": payload["domain"],
            "prerequisites": payload["prerequisites"],
            "development": {
                "development_stages": payload["development_stages"],
                "paper_guidance": payload["paper_guidance"],
            },
            "evidence_claims": payload["evidence_claims"],
        }
        model = FakeJSONModel([development, payload, payload])

        result = StructuredOnboardingGenerator(model, config).generate_incrementally(
            DomainOnboardingRequest(query="RAG"),
            make_profile(),
            make_plan(),
            ranked,
            lambda *_: None,
        )

        self.assertEqual(len(result.output.prerequisites), 3)
        self.assertEqual(len(result.output.development_stages), 3)
        self.assertTrue(result.output.papers[0].contribution)

    def test_incremental_generation_rebinds_invalid_breakthrough_paper_ids(self) -> None:
        config = DomainOnboardingConfig()
        ranked = WeightedPaperRanker(config).rank(
            make_candidates(), make_plan(), limit=6
        ).papers
        payload = make_generation_payload([paper.paper_id for paper in ranked])
        payload["development_stages"][0]["related_paper_ids"] = ["invented-paper"]
        payload["development_stages"][0]["breakthroughs"][0][
            "supporting_paper_ids"
        ] = ["invented-paper"]
        model = FakeJSONModel([payload, payload, payload])

        result = StructuredOnboardingGenerator(model, config).generate_incrementally(
            DomainOnboardingRequest(query="RAG"),
            make_profile(),
            make_plan(),
            ranked,
            lambda *_: None,
        )

        allowed_ids = {paper.paper_id for paper in ranked}
        stage = result.output.development_stages[0]
        self.assertTrue(set(stage.related_paper_ids) <= allowed_ids)
        self.assertTrue(
            set(stage.breakthroughs[0].supporting_paper_ids) <= allowed_ids
        )
        self.assertNotIn("invented-paper", stage.related_paper_ids)

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
        core_references = [
            reference
            for stage in output.development_stages
            for reference in stage.representative_papers
            if reference.reading_priority == "core"
        ]
        self.assertTrue(core_references)
        self.assertTrue(all(item.contribution for item in core_references))
        self.assertTrue(all(item.reading_focus for item in core_references))

    def test_generator_sends_compact_grounding_context_and_token_limit(self) -> None:
        config = self.config.model_copy(
            update={
                "generation_paper_abstract_max_chars": 200,
                "generation_max_tokens": 4321,
            }
        )
        ranked = list(self.ranked)
        ranked[0] = ranked[0].model_copy(update={"abstract": "x" * 1000})
        model = FakeJSONModel(
            [make_generation_payload([paper.paper_id for paper in ranked])]
        )

        StructuredOnboardingGenerator(model, config).generate(
            DomainOnboardingRequest(query="RAG"),
            make_profile(),
            make_plan(),
            ranked,
        )

        call = model.calls[0]
        prompt = json.loads(call["messages"][1]["content"])
        paper = prompt["allowed_papers"][0]
        self.assertEqual(call["max_tokens"], 4321)
        self.assertEqual(len(paper["abstract"]), 200)
        self.assertNotIn("authors", paper)
        self.assertNotIn("url", paper)

    def test_learning_path_is_fixed_order_without_hardcoded_activities(self) -> None:
        payload = make_generation_payload([paper.paper_id for paper in self.ranked])
        generator = StructuredOnboardingGenerator(FakeJSONModel([payload]), self.config)
        output = generator.generate(
            DomainOnboardingRequest(query="RAG"),
            make_profile("experiment_first"),
            make_plan(),
            self.ranked,
        ).output
        self.assertEqual([step.step for step in output.learning_path], ["1", "2", "3", "4", "5"])
        expected_activities = [
            step["activities"] for step in payload["learning_path"]
        ]
        self.assertEqual(
            [step.activities for step in output.learning_path],
            expected_activities,
        )
        self.assertTrue(all(step.completion_criteria for step in output.learning_path))

    def test_generator_does_not_replace_short_summary_with_filler(self) -> None:
        payload = make_generation_payload([paper.paper_id for paper in self.ranked])
        payload["text"] = "RAG 入门计划"

        output = StructuredOnboardingGenerator(
            FakeJSONModel([payload]), self.config
        ).generate(
            DomainOnboardingRequest(query="RAG"),
            make_profile(),
            make_plan(),
            self.ranked,
        ).output

        self.assertEqual(output.text, "RAG 入门计划")

    def test_learning_path_keeps_application_and_frontier_papers_out_of_first_two_steps(self) -> None:
        ranked = [paper.model_copy(deep=True) for paper in self.ranked]
        ranked[0].paper_role = "application"
        ranked[0].reading_priority = "optional"
        ranked[1].paper_role = "survey"
        payload = make_generation_payload([paper.paper_id for paper in ranked])
        for step in payload["learning_path"][:2]:
            step["paper_ids"] = [ranked[0].paper_id]

        output = StructuredOnboardingGenerator(
            FakeJSONModel([payload]), self.config
        ).generate(
            DomainOnboardingRequest(query="RAG"),
            make_profile(),
            make_plan(),
            ranked,
        ).output
        role_by_id = {paper.paper_id: paper.paper_role for paper in ranked}

        self.assertTrue(output.learning_path[0].paper_ids)
        self.assertTrue(output.learning_path[1].paper_ids)
        self.assertFalse(
            {
                role_by_id[paper_id]
                for step in output.learning_path[:2]
                for paper_id in step.paper_ids
            }
            & {"application", "frontier"}
        )

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

    def test_object_subdirections_are_normalized_to_names(self) -> None:
        payload = make_generation_payload([paper.paper_id for paper in self.ranked])
        payload["current_landscape"]["subdirections"] = [
            {"name": "理论机制", "description": "details"},
            {"name": "异构架构"},
            {"name": "理论机制"},
        ]

        output = StructuredOnboardingGenerator(
            FakeJSONModel([payload]), self.config
        ).generate(
            DomainOnboardingRequest(query="multi-agent debate"),
            make_profile(),
            make_plan(),
            self.ranked,
        ).output

        self.assertEqual(
            output.current_landscape.subdirections,
            ["理论机制", "异构架构"],
        )
        self.assertTrue(
            all(not item.startswith("{") for item in output.current_landscape.subdirections)
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
                "paper_relevance",
                "evidence_grounding",
                "topic_coverage",
                "development_coherence",
                "learning_path",
                "goal_alignment",
                "language_alignment",
            },
        )
        self.assertEqual(quality.state, "warning")
        self.assertEqual(
            {gate.gate: gate.status for gate in quality.hard_gates},
            {
                "required_structure": "passed",
                "paper_identity": "passed",
                "paper_relevance": "passed",
                "evidence_support": "passed",
            },
        )

    def test_development_stages_are_ordered_and_linked_for_frontend(self) -> None:
        stages = self.output.development_stages

        self.assertEqual([stage.sequence for stage in stages], [1, 2, 3])
        self.assertIsNone(stages[0].previous_stage_id)
        self.assertTrue(all(stage.period for stage in stages))
        for previous, current in zip(stages, stages[1:]):
            self.assertEqual(current.previous_stage_id, previous.stage_id)
            self.assertTrue(current.transition_from_previous)

    def test_landscape_is_structured_and_grounded(self) -> None:
        landscape = self.output.current_landscape

        self.assertEqual(
            [item.name for item in landscape.problem_details],
            landscape.problems,
        )
        self.assertEqual(
            [item.name for item in landscape.subdirection_details],
            landscape.subdirections,
        )
        self.assertTrue(
            all(item.related_paper_ids and item.related_stage_ids for item in landscape.problem_details)
        )
        self.assertTrue(
            all(
                item.description
                and item.why_it_matters
                and item.research_questions
                and item.related_paper_ids
                and item.related_stage_ids
                for item in landscape.subdirection_details
            )
        )

    def test_learning_path_uses_complete_time_budget(self) -> None:
        steps = self.output.learning_path

        self.assertEqual(steps[0].start_week, 1)
        self.assertEqual(steps[-1].end_week, 6)
        self.assertTrue(all(step.milestone and step.estimated_hours for step in steps))
        self.assertTrue(
            all(
                current.start_week >= previous.start_week
                and current.end_week >= previous.end_week
                for previous, current in zip(steps, steps[1:])
            )
        )
    def test_modified_paper_metadata_fails_hard_gate(self) -> None:
        self.output.papers[0].title = "Model invented title"
        quality = self.evaluator.evaluate(self.output, self.ranked)
        self.assertFalse(quality.passed_hard_gates)
        self.assertEqual(quality.state, "failed")
        self.assertEqual(
            next(gate for gate in quality.hard_gates if gate.gate == "paper_identity").status,
            "failed",
        )
        self.assertTrue(any(issue.issue_type == "invalid_paper" for issue in quality.issues))

    def test_content_completeness_cannot_offset_invalid_paper(self) -> None:
        self.output.learning_path[0].paper_ids.append("outside-candidate-set")
        quality = self.evaluator.evaluate(self.output, self.ranked)
        self.assertFalse(quality.passed_hard_gates)
        self.assertEqual(quality.dimensions["paper_validity"], 1.0)

    def test_real_but_irrelevant_papers_fail_relevance_hard_gate(self) -> None:
        irrelevant = [paper.model_copy(update={"relevance_score": 0.0}) for paper in self.ranked]

        quality = self.evaluator.evaluate(self.output, irrelevant)

        self.assertFalse(quality.passed_hard_gates)
        self.assertEqual(quality.dimensions["paper_validity"], 1.0)
        self.assertEqual(quality.dimensions["paper_relevance"], 0.0)
        self.assertTrue(any(issue.issue_type == "low_paper_relevance" for issue in quality.issues))

    def test_missing_canonical_reading_fails_relevance_gate(self) -> None:
        noncanonical = [
            paper
            for paper in self.ranked
            if "Knowledge-Intensive NLP Tasks" not in paper.title
        ]
        payload = make_generation_payload([paper.paper_id for paper in noncanonical])
        output = StructuredOnboardingGenerator(
            FakeJSONModel([payload]), self.config
        ).generate(
            DomainOnboardingRequest(query="RAG"),
            make_profile(),
            make_plan(),
            noncanonical,
        ).output

        quality = self.evaluator.evaluate(output, noncanonical)

        self.assertFalse(quality.passed_hard_gates)
        self.assertTrue(
            any(issue.issue_type == "missing_core_paper" for issue in quality.issues)
        )

    def test_missing_paper_guidance_reduces_stage_quality(self) -> None:
        reference = self.output.development_stages[0].representative_papers[0]
        reference.contribution = ""
        reference.reading_focus = []

        quality = self.evaluator.evaluate(self.output, self.ranked)

        self.assertLess(quality.dimensions["development_coherence"], 1.0)
        self.assertTrue(
            any(issue.issue_type == "weak_development_stage" for issue in quality.issues)
        )

    def test_numeric_hard_gate_fails_even_when_only_warning_issue_exists(self) -> None:
        gate_results = self.evaluator._hard_gate_results(
            {
                "structure": 1.0,
                "paper_validity": 1.0,
                "paper_relevance": 0.59,
                "evidence_grounding": 1.0,
            },
            [],
        )

        relevance_gate = next(
            gate for gate in gate_results if gate.gate == "paper_relevance"
        )
        self.assertEqual(relevance_gate.status, "failed")
        self.assertEqual(relevance_gate.score, 0.59)
        self.assertEqual(relevance_gate.threshold, 0.60)

    def test_explicit_claim_without_abstract_fails_evidence_gate(self) -> None:
        ranked = [
            paper.model_copy(update={"abstract": None}) for paper in self.ranked
        ]
        self.output.evidence_claims = [
            EvidenceClaim(
                claim="retrieval augmented generation method benchmark evaluation",
                supporting_paper_ids=[ranked[0].paper_id],
                support_type="abstract_explicit",
            )
        ]

        quality = self.evaluator.evaluate(self.output, ranked)

        self.assertFalse(quality.passed_hard_gates)
        self.assertTrue(
            any("缺少可验证摘要" in issue.message for issue in quality.issues)
        )

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

    def test_cross_language_explicit_claim_uses_terminology_bridge(self) -> None:
        self.output.evidence_claims = [
            EvidenceClaim(
                claim="该方法通过检索外部证据增强生成结果",
                supporting_paper_ids=[paper.paper_id for paper in self.ranked[:3]],
                support_type="abstract_explicit",
            )
        ]

        quality = self.evaluator.evaluate(self.output, self.ranked)

        self.assertTrue(quality.passed_hard_gates)
        self.assertEqual(quality.evidence_validation_modes, {"terminology_bridge": 1})
        self.assertFalse(any(issue.issue_type == "unsupported_claim" for issue in quality.issues))

    def test_cross_language_unrelated_explicit_claim_fails_after_bridge(self) -> None:
        self.output.evidence_claims = [
            EvidenceClaim(
                claim="图神经网络方法证明节点分类最先进",
                supporting_paper_ids=[paper.paper_id for paper in self.ranked[:2]],
                support_type="abstract_explicit",
            )
        ]

        quality = self.evaluator.evaluate(self.output, self.ranked)

        self.assertFalse(quality.passed_hard_gates)
        issue = next(issue for issue in quality.issues if issue.issue_type == "unsupported_claim")
        self.assertEqual(issue.severity, "error")
        self.assertEqual(quality.evidence_validation_modes, {"terminology_bridge": 1})

    def test_evidence_embedding_failure_falls_back_to_terminology_bridge(self) -> None:
        class FailingEmbedding:
            name = "embedding"

            def vectorize(self, texts):
                raise RuntimeError("embedding unavailable")

        self.output.evidence_claims = [
            EvidenceClaim(
                claim="该方法通过检索证据增强生成结果",
                supporting_paper_ids=[paper.paper_id for paper in self.ranked[:2]],
                support_type="abstract_explicit",
            )
        ]
        evaluator = CompositeQualityEvaluator(
            self.config, evidence_vectorizer=FailingEmbedding()
        )

        quality = evaluator.evaluate(self.output, self.ranked)

        self.assertTrue(quality.passed_hard_gates)
        self.assertEqual(quality.evidence_validation_modes, {"terminology_bridge": 1})


class RepairTests(unittest.TestCase):
    def test_repair_planner_links_actions_to_quality_issues(self) -> None:
        quality = CompositeQualityEvaluator(DomainOnboardingConfig()).evaluate(
            self._invalid_output(),
            self._ranked(),
        )

        plan = RepairPlanner().plan(quality, max_content_repairs=1)

        self.assertTrue(plan.actions)
        self.assertTrue(all(action.issue_ids for action in plan.actions))
        self.assertTrue(all(action.target_paths for action in plan.actions))
        self.assertEqual(
            {action.action_type for action in plan.actions},
            {"code", "llm"},
        )

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
        self.assertTrue(repair_result.record.triggered)
        self.assertEqual(repair_result.record.actions[0].status, "applied")
        self.assertTrue(
            any(action.status == "skipped" for action in repair_result.record.actions)
        )

    def test_code_repair_preserves_reading_guidance_and_priority(self) -> None:
        config = DomainOnboardingConfig(max_content_repairs=0)
        ranked = WeightedPaperRanker(config).rank(
            make_candidates(), make_plan(), limit=6
        ).papers
        payload = make_generation_payload([paper.paper_id for paper in ranked])
        generator = StructuredOnboardingGenerator(FakeJSONModel([payload]), config)
        output = generator.generate(
            DomainOnboardingRequest(query="RAG"),
            make_profile(),
            make_plan(),
            ranked,
        ).output
        expected_metadata = {
            reference.paper_id: (
                reference.reading_priority,
                reference.is_canonical,
            )
            for reference in [
                *output.development_stages[0].representative_papers,
                *output.learning_path[0].papers,
            ]
        }

        repaired = TargetedRepairer(generator, config).code_executor.execute(
            output, ranked
        )

        references = [
            *repaired.development_stages[0].representative_papers,
            *repaired.learning_path[0].papers,
        ]
        self.assertTrue(all(reference.contribution for reference in references))
        self.assertTrue(all(reference.reading_focus for reference in references))
        self.assertTrue(
            all(
                (reference.reading_priority, reference.is_canonical)
                == expected_metadata[reference.paper_id]
                for reference in references
            )
        )
        self.assertEqual(
            repaired.learning_path[0].paper_bindings[0].learning_use,
            "concept_introduction",
        )

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
        self.assertEqual(repair_result.record.actions[-1].status, "skipped")
        self.assertIn("repair_issues", model.calls[-1]["messages"][1]["content"])

    def test_failed_llm_repair_is_recorded_with_error(self) -> None:
        config = DomainOnboardingConfig()
        ranked = self._ranked()
        output = self._invalid_output()
        quality = CompositeQualityEvaluator(config).evaluate(output, ranked)
        generator = StructuredOnboardingGenerator(FakeJSONModel(["not json"]), config)

        result = TargetedRepairer(generator, config).repair(
            DomainOnboardingRequest(query="RAG"),
            make_profile(),
            make_plan(),
            output,
            quality,
            ranked,
        )

        llm_action = next(
            action for action in result.record.actions if action.action_type == "llm"
        )
        self.assertEqual(result.action, "llm_repair_failed")
        self.assertEqual(llm_action.status, "failed")
        self.assertTrue(llm_action.error)

    @staticmethod
    def _ranked():
        config = DomainOnboardingConfig()
        return WeightedPaperRanker(config).rank(
            make_candidates(), make_plan(), limit=6
        ).papers

    @classmethod
    def _invalid_output(cls):
        config = DomainOnboardingConfig()
        ranked = cls._ranked()
        payload = make_generation_payload([paper.paper_id for paper in ranked])
        output = StructuredOnboardingGenerator(
            FakeJSONModel([payload]), config
        ).generate(
            DomainOnboardingRequest(query="RAG"),
            make_profile(),
            make_plan(),
            ranked,
        ).output
        output.text = "太短"
        output.development_stages = output.development_stages[:1]
        return output


if __name__ == "__main__":
    unittest.main()
