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
    DevelopmentStageResearchPlan,
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
    def test_staged_development_generates_each_researched_stage_separately(self) -> None:
        ranked = WeightedPaperRanker(DomainOnboardingConfig()).rank(
            make_candidates(), make_plan(), limit=6
        ).papers
        paper_ids = [paper.paper_id for paper in ranked]
        base = make_generation_payload(paper_ids)

        class PieceModel:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def chat(self, **kwargs):
                self.calls.append(kwargs)
                system = kwargs["messages"][0]["content"]
                user = json.loads(kwargs["messages"][1]["content"])
                if "foundation block" in system:
                    payload = {
                        "domain": base["domain"],
                        "text": base["text"],
                        "prerequisites": base["prerequisites"],
                        "paper_guidance": [],
                        "evidence_claims": [],
                    }
                else:
                    plan_payload = user["stage_research_plan"]
                    sequence = int(plan_payload["sequence"])
                    stage = json.loads(
                        json.dumps(base["development_stages"][sequence - 1])
                    )
                    selected_ids = plan_payload["selected_paper_ids"]
                    stage["related_paper_ids"] = selected_ids
                    for detail in [
                        *stage["core_concepts"],
                        *stage["main_techniques"],
                    ]:
                        detail["related_paper_ids"] = selected_ids
                    stage["breakthroughs"][0]["supporting_paper_ids"] = selected_ids
                    if sequence == 1:
                        breakthrough = stage.pop("breakthroughs")[0]
                        breakthrough["paper_ids"] = breakthrough.pop(
                            "supporting_paper_ids"
                        )
                        stage["breakthrough"] = breakthrough
                    payload = {
                        "development_stage": stage,
                        "paper_guidance": [
                            item
                            for item in base["paper_guidance"]
                            if item["paper_id"] in selected_ids
                        ],
                        "evidence_claims": [],
                    }
                    if sequence == 1:
                        payload = stage
                return {
                    "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                }

        plan = make_plan()
        plan.development_stage_plans = [
            DevelopmentStageResearchPlan(
                stage_id=f"researched-{index}",
                sequence=index,
                name=f"研究阶段 {index}",
                period=f"20{index}0-20{index}3",
                focus=f"阶段 {index} 重点",
                transition_from_previous="" if index == 1 else "上一阶段局限推动方法演进",
                search_queries=[f"RAG stage {index}"],
                selected_paper_ids=[paper_ids[index - 1]],
            )
            for index in range(1, 4)
        ]
        model = PieceModel()
        generator = StructuredOnboardingGenerator(model, DomainOnboardingConfig())

        payload, stats = generator._call_staged_development(
            DomainOnboardingRequest(query="检索增强生成"),
            plan,
            ranked,
            None,
        )

        self.assertEqual(len(model.calls), 4)
        self.assertEqual(
            [stage["stage_id"] for stage in payload["development_stages"]],
            ["researched-1", "researched-2", "researched-3"],
        )
        self.assertEqual(stats.model_calls, 4)
        self.assertEqual(len(payload["development_stages"][0]["breakthroughs"]), 1)

    def test_development_stage_planner_creates_chronological_search_contract(self) -> None:
        model = FakeJSONModel(
            [
                {
                    "development_stage_plans": [
                        {
                            "stage_id": f"era-{index}",
                            "sequence": index,
                            "name": f"阶段 {index}",
                            "period": f"20{index}0-20{index}3",
                            "focus": f"阶段 {index} 的问题与方法",
                            "transition_from_previous": "" if index == 1 else "新方法解决了上一阶段的局限",
                            **(
                                {"search_query": f"retrieval augmented generation era {index} method"}
                                if index == 1
                                else {"search_queries": [f"retrieval augmented generation era {index} method"]}
                            ),
                        }
                        for index in range(1, 4)
                    ]
                }
            ]
        )
        generator = StructuredOnboardingGenerator(model, DomainOnboardingConfig())
        ranked = WeightedPaperRanker(DomainOnboardingConfig()).rank(
            make_candidates(), make_plan(), limit=6
        ).papers

        stages, stats = generator.plan_development_research(
            DomainOnboardingRequest(query="检索增强生成"),
            make_plan(),
            ranked,
        )

        self.assertEqual([stage.sequence for stage in stages], [1, 2, 3])
        self.assertTrue(all(stage.search_queries for stage in stages))
        self.assertTrue(all(not stage.selected_paper_ids for stage in stages))
        self.assertEqual(stats.model_calls, 1)
        self.assertIn("Do not include paper IDs", model.calls[0]["messages"][0]["content"])

    def test_concepts_and_techniques_preserve_explanations_and_paper_links(self) -> None:
        config = DomainOnboardingConfig(generation_retry_backoff_seconds=0)
        ranked = WeightedPaperRanker(config).rank(
            make_candidates(), make_plan(), limit=6
        ).papers
        payload = make_generation_payload([paper.paper_id for paper in ranked])
        paper_id = ranked[0].paper_id
        payload["prerequisites"][0]["key_points"] = [
            {
                "name": "向量检索",
                "explanation": "把查询和文档映射到同一向量空间并寻找近邻。",
                "why_it_matters": "它决定外部证据能否被正确召回。",
                "related_paper_ids": [paper_id, "invalid"],
            }
        ]
        payload["development_stages"][0]["core_concepts"] = [
            {
                "name": "非参数记忆",
                "explanation": "模型在参数之外读取可更新的知识库。",
                "related_paper_ids": [paper_id],
            }
        ]
        payload["development_stages"][0]["main_techniques"] = [
            {
                "name": "检索增强生成",
                "explanation": "先检索证据，再让生成器基于证据作答。",
                "mechanism": "检索器与序列生成器协同工作。",
                "related_paper_ids": [paper_id],
            }
        ]

        model = FakeJSONModel([payload])
        output = StructuredOnboardingGenerator(model, config).generate(
            DomainOnboardingRequest(query="我有六周时间，偏向实验地学习 RAG"),
            make_profile("experiment_first"),
            make_plan(),
            ranked,
        ).output

        self.assertEqual(output.schema_version, "domain-onboarding-output-v1.9")
        self.assertEqual(output.prerequisites[0].key_points[0].explanation, "把查询和文档映射到同一向量空间并寻找近邻。")
        self.assertEqual(output.prerequisites[0].key_points[0].related_paper_ids, [paper_id])
        self.assertTrue(output.development_stages[0].core_concepts[0].explanation)
        self.assertTrue(output.development_stages[0].main_techniques[0].mechanism)
        self.assertEqual(output.learner_profile.preference, "balanced")
        self.assertIsNone(output.learner_profile.time_budget_weeks)
        user_payload = model.calls[0]["messages"][1]["content"]
        self.assertNotIn("learner_profile", user_payload)
        self.assertNotIn("六周", user_payload)
        self.assertIn(
            "# 领域入门研究导航",
            model.calls[0]["messages"][0]["content"],
        )

    def test_incremental_generation_emits_validated_sections_in_display_order(self) -> None:
        config = DomainOnboardingConfig(generation_retry_backoff_seconds=0)
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
        config = DomainOnboardingConfig(generation_retry_backoff_seconds=0)
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
        self.assertGreaterEqual(len(model.calls), 1)

    def test_incremental_generation_identifies_development_failure(self) -> None:
        config = DomainOnboardingConfig(generation_retry_backoff_seconds=0)
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

    def test_incremental_generation_retries_invalid_section_json(self) -> None:
        config = DomainOnboardingConfig(generation_retry_backoff_seconds=0)
        ranked = WeightedPaperRanker(config).rank(
            make_candidates(), make_plan(), limit=6
        ).papers
        payload = make_generation_payload([paper.paper_id for paper in ranked])
        model = FakeJSONModel(["not json", payload, payload, payload])
        events = []

        result = StructuredOnboardingGenerator(model, config).generate_incrementally(
            DomainOnboardingRequest(query="RAG"),
            make_profile(),
            make_plan(),
            ranked,
            lambda event, data, paths: events.append((event, data, paths)),
        )

        self.assertEqual(len(model.calls), 4)
        self.assertIn(
            "previous response could not be parsed or validated",
            model.calls[1]["messages"][0]["content"].lower(),
        )
        self.assertEqual(
            [item[0] for item in events],
            ["development_ready", "landscape_ready", "learning_path_ready"],
        )
        self.assertEqual(len(result.output.development_stages), 3)

    def test_incremental_generation_keeps_valid_sections_when_optional_section_fails(self) -> None:
        config = DomainOnboardingConfig(generation_retry_backoff_seconds=0)
        ranked = WeightedPaperRanker(config).rank(
            make_candidates(), make_plan(), limit=6
        ).papers
        payload = make_generation_payload([paper.paper_id for paper in ranked])
        model = FakeJSONModel(
            [payload, "not json", "not json", "not json", payload]
        )
        events = []

        result = StructuredOnboardingGenerator(model, config).generate_incrementally(
            DomainOnboardingRequest(query="RAG"),
            make_profile(),
            make_plan(),
            ranked,
            lambda event, data, paths: events.append((event, data, paths)),
        )

        self.assertEqual(
            [item[0] for item in events],
            ["development_ready", "learning_path_ready"],
        )
        self.assertEqual(len(result.output.development_stages), 3)
        self.assertEqual(len(result.output.learning_path), 5)
        self.assertEqual(result.output.current_landscape.problems, [])

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

    def test_landscape_section_wraps_real_top_level_landscape_fields(self) -> None:
        config = DomainOnboardingConfig()
        ranked = WeightedPaperRanker(config).rank(
            make_candidates(), make_plan(), limit=6
        ).papers
        payload = make_generation_payload([paper.paper_id for paper in ranked])[
            "current_landscape"
        ]
        generator = StructuredOnboardingGenerator(FakeJSONModel([]), config)

        completed = generator._complete_section_payload(
            "landscape", payload, ranked
        )

        self.assertEqual(
            completed["current_landscape"]["problems"], payload["problems"]
        )
        self.assertEqual(
            completed["current_landscape"]["subdirections"],
            payload["subdirections"],
        )

    def test_landscape_section_rejects_empty_detail_arrays(self) -> None:
        config = DomainOnboardingConfig()
        ranked = WeightedPaperRanker(config).rank(
            make_candidates(), make_plan(), limit=6
        ).papers
        generator = StructuredOnboardingGenerator(FakeJSONModel([]), config)

        with self.assertRaisesRegex(
            GenerationError,
            "requires 3 non-empty problem_details",
        ):
            generator._complete_section_payload(
                "landscape",
                {
                    "current_landscape": {
                        "problems": ["问题一", "问题二", "问题三"],
                        "subdirections": ["方向一", "方向二", "方向三"],
                        "problem_details": [],
                        "subdirection_details": [],
                    }
                },
                ranked,
            )

    def test_landscape_section_rejects_internal_detail_names(self) -> None:
        config = DomainOnboardingConfig()
        ranked = WeightedPaperRanker(config).rank(
            make_candidates(), make_plan(), limit=6
        ).papers
        payload = make_generation_payload([paper.paper_id for paper in ranked])
        payload["current_landscape"]["problem_details"][0]["name"] = (
            "problem_optimization"
        )
        generator = StructuredOnboardingGenerator(FakeJSONModel([]), config)

        with self.assertRaisesRegex(
            GenerationError,
            "reader-facing labels, not internal IDs",
        ):
            generator._complete_section_payload("landscape", payload, ranked)

    def test_learning_path_section_accepts_real_step_list_aliases(self) -> None:
        config = DomainOnboardingConfig()
        ranked = WeightedPaperRanker(config).rank(
            make_candidates(), make_plan(), limit=6
        ).papers
        steps = make_generation_payload([paper.paper_id for paper in ranked])[
            "learning_path"
        ]
        generator = StructuredOnboardingGenerator(FakeJSONModel([]), config)

        for alias in ("steps", "learning_steps", "path"):
            with self.subTest(alias=alias):
                completed = generator._complete_section_payload(
                    "learning_path", {alias: steps}, ranked
                )
                self.assertEqual(completed["learning_path"], steps)

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

    def test_generator_sends_compact_grounding_context_without_output_limit(self) -> None:
        config = self.config.model_copy(
            update={
                "generation_paper_abstract_max_chars": 200,
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
        self.assertNotIn("max_tokens", call)
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

    def test_evidence_claims_accept_only_literal_selected_ids_from_evidence_text(self) -> None:
        payload = make_generation_payload([paper.paper_id for paper in self.ranked])
        selected_id = self.ranked[0].paper_id
        payload["evidence_claims"] = [
            {
                "claim": "retrieval augmented generation uses external evidence",
                "evidence": f"{selected_id} reports this result; invented-paper does not count.",
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
            [selected_id],
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
                "language_alignment",
            },
        )
        self.assertEqual(quality.state, "passed")
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
                and item.typical_tasks
                and item.prerequisites
                and item.common_techniques
                and all(
                    technique.explanation
                    and technique.mechanism
                    and technique.related_paper_ids
                    for technique in item.common_techniques
                )
                and item.datasets_and_benchmarks
                and item.evaluation_metrics
                and item.starter_project
                and item.research_workflow
                and item.research_questions
                and item.related_paper_ids
                and item.related_stage_ids
                for item in landscape.subdirection_details
            )
        )

    def test_learning_path_does_not_assume_a_personal_time_budget(self) -> None:
        steps = self.output.learning_path

        self.assertTrue(all(step.start_week is None for step in steps))
        self.assertTrue(all(step.end_week is None for step in steps))
        self.assertTrue(all(step.milestone and step.estimated_hours for step in steps))
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

            def __init__(self):
                self.calls = 0

            def vectorize(self, texts):
                self.calls += 1
                raise RuntimeError("embedding unavailable")

        self.output.evidence_claims = [
            EvidenceClaim(
                claim="该方法通过检索证据增强生成结果",
                supporting_paper_ids=[paper.paper_id for paper in self.ranked[:2]],
                support_type="abstract_explicit",
            ),
            EvidenceClaim(
                claim="该系统使用检索上下文评估生成结果",
                supporting_paper_ids=[paper.paper_id for paper in self.ranked[:2]],
                support_type="abstract_explicit",
            ),
        ]
        embedding = FailingEmbedding()
        evaluator = CompositeQualityEvaluator(
            self.config, evidence_vectorizer=embedding
        )

        quality = evaluator.evaluate(self.output, self.ranked)

        self.assertTrue(quality.passed_hard_gates)
        self.assertEqual(embedding.calls, 2)
        self.assertEqual(
            quality.evidence_validation_modes,
            {"terminology_bridge": 2},
        )

    def test_named_remote_embedding_resolves_cross_language_evidence(self) -> None:
        class RemoteEmbedding:
            name = "embedding:qwen3-embedding"

            def vectorize(self, texts):
                return [{"semantic": 1.0} for _ in texts]

        self.output.evidence_claims = [
            EvidenceClaim(
                claim="该方法通过检索外部证据增强生成结果",
                supporting_paper_ids=[paper.paper_id for paper in self.ranked[:3]],
                support_type="abstract_explicit",
            )
        ]
        evaluator = CompositeQualityEvaluator(
            self.config, evidence_vectorizer=RemoteEmbedding()
        )

        quality = evaluator.evaluate(self.output, self.ranked)

        self.assertTrue(quality.passed_hard_gates)
        self.assertEqual(quality.evidence_validation_modes, {"multilingual_embedding": 1})


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
            all(action.status != "failed" for action in repair_result.record.actions)
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
                        issue_type="weak_development_stage",
                        severity="warning",
                        target_path="development_stages[1]",
                        message="stage is incomplete",
                        recommended_action="rewrite stage",
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
