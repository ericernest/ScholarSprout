from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

from channels.base import ChannelMessage
from handlers.domain_onboarding.config import DomainOnboardingConfig
from handlers.domain_onboarding.execution import PipelineExecutionContext
from handlers.domain_onboarding.generator import GenerationError, StructuredOnboardingGenerator
from handlers.domain_onboarding.metrics import DomainOnboardingMetrics, DomainOnboardingRequestTrace
from handlers.domain_onboarding.pipeline import DomainOnboardingPipeline
from handlers.domain_onboarding.profile import RuleBasedProfileBuilder
from handlers.domain_onboarding.quality import CompositeQualityEvaluator
from handlers.domain_onboarding.ranking import WeightedPaperRanker
from handlers.domain_onboarding.repair import TargetedRepairer
from handlers.domain_onboarding.retrieval import PaperRetrievalError
from handlers.domain_onboarding.schemas import (
    ContentQuality,
    DevelopmentStageResearchPlan,
    DomainOnboardingRequest,
    GenerationResult,
    ModelCallStats,
    PaperSearchQuery,
    PlanningResult,
    QualityGateResult,
    QualityIssue,
    RetrievalResult,
    RetrievalStats,
)
from handlers.domain_onboarding_handler import handle_domain_onboarding_message

from .fakes import FakeJSONModel, make_candidates, make_generation_payload, make_plan


class FakePlanner:
    def plan(self, query: str, profile: object) -> object:
        return PlanningResult(plan=make_plan())


class FakeRetriever:
    def __init__(self, *, fail: bool = False, empty: bool = False) -> None:
        self.fail = fail
        self.empty = empty
        self.calls = 0
        self.query_batches: list[list[str]] = []

    def search(self, queries: list[str], *, limit_per_query: int) -> RetrievalResult:
        self.calls += 1
        self.query_batches.append(list(queries))
        if self.fail:
            raise PaperRetrievalError(
                "all paper queries failed",
                stats=RetrievalStats(errors=["offline"]),
            )
        return RetrievalResult(papers=[] if self.empty else make_candidates())


def make_pipeline(
    responses: list[object],
    *,
    fail_retrieval: bool = False,
    empty_retrieval: bool = False,
    config: DomainOnboardingConfig | None = None,
    repair_advisor: object | None = None,
) -> DomainOnboardingPipeline:
    config = (config or DomainOnboardingConfig()).model_copy(
        update={"staged_development_enabled": False}
    )
    generator = StructuredOnboardingGenerator(FakeJSONModel(responses), config)
    return DomainOnboardingPipeline(
        profile_builder=RuleBasedProfileBuilder(),
        planner=FakePlanner(),
        retriever=FakeRetriever(fail=fail_retrieval, empty=empty_retrieval),
        ranker=WeightedPaperRanker(config),
        generator=generator,
        evaluator=CompositeQualityEvaluator(config),
        repairer=TargetedRepairer(generator, config),
        config=config,
        repair_advisor=repair_advisor,
    )


class PipelineTests(unittest.TestCase):
    def test_embedding_degraded_evidence_gate_skips_full_llm_repair(self) -> None:
        paper_ids = [paper.paper_id for paper in make_candidates()]
        payload = make_generation_payload(paper_ids)
        pipeline = make_pipeline([payload, payload, payload])

        issue = QualityIssue(
            issue_type="unsupported_claim",
            severity="error",
            target_path="evidence_claims[0].claim",
            message="embedding 降级后证据相似度不足",
            recommended_action="稍后重新进行语义校验",
        )

        class DegradedEvaluator:
            def evaluate(self, output, papers):
                return ContentQuality(
                    score=0.85,
                    threshold=0.75,
                    passed_hard_gates=False,
                    dimensions={"evidence_grounding": 0.45},
                    issues=[issue],
                    hard_gates=[
                        QualityGateResult(
                            gate="evidence_support",
                            status="failed",
                            issue_ids=[issue.issue_id],
                            score=0.45,
                            threshold=0.7,
                        )
                    ],
                    evidence_validation_modes={
                        "terminology_bridge": 2,
                        "embedding_fallback": 2,
                    },
                )

        class ForbiddenRepairer:
            def repair(self, *args, **kwargs):
                raise AssertionError("degraded evidence must not trigger full repair")

        pipeline.evaluator = DegradedEvaluator()
        pipeline.repairer = ForbiddenRepairer()
        trace = DomainOnboardingRequestTrace()

        result = pipeline.run(DomainOnboardingRequest(query="RAG"), trace)

        self.assertEqual(result.status, "quality_warning")
        self.assertEqual(len(result.quality_attempts), 1)
        self.assertFalse(result.repair_record.triggered)
        self.assertEqual(
            result.repair_record.decision.reasons,
            ["evidence_validation_degraded"],
        )
        self.assertEqual(
            result.final_quality.selection_reason,
            "evidence_validation_degraded",
        )

    def test_retrieved_papers_are_annotated_with_query_role_and_path(self) -> None:
        paper = make_candidates(1)[0].model_copy(
            update={"matched_queries": ["RAG survey"]}
        )
        annotated = DomainOnboardingPipeline._annotate_candidate_query_hints(
            [paper],
            [
                PaperSearchQuery(
                    query="RAG survey",
                    role_hint="survey",
                    path_id="foundations",
                )
            ],
        )

        self.assertEqual(annotated[0].matched_role_hints, ["survey"])
        self.assertEqual(annotated[0].matched_path_hints, ["foundations"])

    def test_stage_planning_json_failure_falls_back_to_standard_generation(self) -> None:
        config = DomainOnboardingConfig(staged_development_enabled=True)
        delegate = StructuredOnboardingGenerator(
            FakeJSONModel([make_generation_payload([paper.paper_id for paper in make_candidates()])]),
            config,
        )

        class StagePlanningFailureGenerator:
            def __init__(self) -> None:
                self.stage_planning_calls = 0
                self.generation_calls = 0

            def plan_development_research(self, *args, **kwargs):
                self.stage_planning_calls += 1
                raise GenerationError("LLM did not return a JSON object")

            def generate(self, *args, **kwargs):
                self.generation_calls += 1
                return delegate.generate(*args, **kwargs)

        generator = StagePlanningFailureGenerator()
        pipeline = DomainOnboardingPipeline(
            profile_builder=RuleBasedProfileBuilder(),
            planner=FakePlanner(),
            retriever=FakeRetriever(),
            ranker=WeightedPaperRanker(config),
            generator=generator,
            evaluator=CompositeQualityEvaluator(config),
            repairer=TargetedRepairer(delegate, config),
            config=config,
        )
        trace = DomainOnboardingRequestTrace()

        result = pipeline.run(DomainOnboardingRequest(query="RAG"), trace)

        self.assertIn(result.status, {"ok", "quality_warning"}, result.error)
        self.assertEqual(generator.stage_planning_calls, 1)
        self.assertEqual(generator.generation_calls, 1)
        self.assertEqual(trace.development_stage_count, 0)

    def test_stage_research_retrieves_and_binds_each_stage_independently(self) -> None:
        pipeline = make_pipeline([make_generation_payload(["paper-0"])])
        pipeline.config.staged_development_enabled = True
        plan = make_plan()
        plan.development_stage_plans = [
            DevelopmentStageResearchPlan(
                stage_id=f"era-{index}",
                sequence=index,
                name=f"阶段 {index}",
                period=f"20{index}0-20{index}3",
                focus=f"阶段 {index} 研究重点",
                transition_from_previous="" if index == 1 else "继承并解决上一阶段局限",
                search_queries=[f"retrieval augmented generation stage {index}"],
            )
            for index in range(1, 4)
        ]
        candidates = make_candidates()
        ranked = WeightedPaperRanker(pipeline.config).rank(
            candidates, plan, limit=6
        ).papers
        trace = DomainOnboardingRequestTrace()
        events: list[str] = []

        merged = pipeline._research_development_stages(
            plan,
            candidates,
            ranked,
            trace,
            PipelineExecutionContext(timeout_seconds=60),
            lambda event, *_: events.append(event),
        )

        self.assertEqual(pipeline.retriever.calls, 3)
        self.assertEqual(events, ["stage_retrieval_ready"] * 3)
        self.assertTrue(all(stage.selected_paper_ids for stage in plan.development_stage_plans))
        self.assertTrue(
            all(
                set(stage.selected_paper_ids) <= {paper.paper_id for paper in merged}
                for stage in plan.development_stage_plans
            )
        )
        self.assertEqual(trace.development_stage_count, 3)
        self.assertEqual(trace.stage_retrieval_query_count, 3)
        self.assertGreater(trace.stage_bound_paper_count, 0)

    def test_cancelled_request_stops_before_first_stage(self) -> None:
        pipeline = make_pipeline([make_generation_payload(["unused-paper"])])
        context = PipelineExecutionContext(timeout_seconds=30)
        context.cancel()
        trace = DomainOnboardingRequestTrace()

        result = pipeline.run(
            DomainOnboardingRequest(query="RAG"),
            trace,
            context,
        )

        self.assertEqual(result.status, "cancelled")
        self.assertEqual(trace.interrupted_stage, "profile")
        self.assertTrue(trace.cancelled)
        self.assertEqual(pipeline.retriever.calls, 0)

    def test_stage_timeout_stops_pipeline_and_records_stage(self) -> None:
        now = [0.0]

        class SlowProfileBuilder:
            def build(self, request):
                now[0] += 1.5
                return RuleBasedProfileBuilder().build(request)

        config = DomainOnboardingConfig(profile_timeout_seconds=1.0)
        pipeline = make_pipeline([{}], config=config)
        pipeline.profile_builder = SlowProfileBuilder()
        context = PipelineExecutionContext(
            timeout_seconds=config.request_timeout_seconds,
            clock=lambda: now[0],
        )
        trace = DomainOnboardingRequestTrace()

        result = pipeline.run(
            DomainOnboardingRequest(query="RAG"),
            trace,
            context,
        )

        self.assertEqual(result.status, "timeout")
        self.assertEqual(trace.interrupted_stage, "profile")
        self.assertTrue(trace.deadline_exceeded)
        self.assertEqual(trace.profile_duration_ms, 1500.0)
        self.assertEqual(pipeline.retriever.calls, 0)

    def test_concurrent_requests_keep_stage_stats_request_scoped(self) -> None:
        retrieval_barrier = Barrier(2)
        generation_barrier = Barrier(2)

        class ConcurrentPlanner:
            def plan(self, query: str, profile: object) -> PlanningResult:
                token_count = 11 if query == "request-a" else 22
                return PlanningResult(
                    plan=make_plan().model_copy(
                        update={"search_queries": [query, "retrieval augmented generation"]}
                    ),
                    stats=ModelCallStats(
                        model_calls=1,
                        total_tokens=token_count,
                        usage_reported=True,
                    ),
                )

        class ConcurrentRetriever:
            def search(self, queries: list[str], *, limit_per_query: int) -> RetrievalResult:
                retrieval_barrier.wait(timeout=2)
                cache_hits = 1 if queries[0] == "request-a" else 2
                return RetrievalResult(
                    papers=make_candidates(),
                    stats=RetrievalStats(
                        request_count=1,
                        cache_hit_count=cache_hits,
                    ),
                )

        class ConcurrentGenerator:
            def generate(self, request, profile, plan, papers) -> GenerationResult:
                generation_barrier.wait(timeout=2)
                generated = StructuredOnboardingGenerator(
                    FakeJSONModel([make_generation_payload([paper.paper_id for paper in papers])]),
                    config,
                ).generate(request, profile, plan, papers)
                token_count = 101 if request.query == "request-a" else 202
                return generated.model_copy(
                    update={
                        "stats": ModelCallStats(
                            model_calls=1,
                            total_tokens=token_count,
                            usage_reported=True,
                        )
                    }
                )

        config = DomainOnboardingConfig()
        generator = ConcurrentGenerator()
        pipeline = DomainOnboardingPipeline(
            profile_builder=RuleBasedProfileBuilder(),
            planner=ConcurrentPlanner(),
            retriever=ConcurrentRetriever(),
            ranker=WeightedPaperRanker(config),
            generator=generator,
            evaluator=CompositeQualityEvaluator(config),
            repairer=TargetedRepairer(generator, config),
            config=config,
        )

        def run(query: str) -> tuple[object, DomainOnboardingRequestTrace]:
            trace = DomainOnboardingRequestTrace()
            return pipeline.run(DomainOnboardingRequest(query=query), trace), trace

        with ThreadPoolExecutor(max_workers=2) as executor:
            future_a = executor.submit(run, "request-a")
            future_b = executor.submit(run, "request-b")
            result_a, trace_a = future_a.result(timeout=5)
            result_b, trace_b = future_b.result(timeout=5)

        self.assertIn(result_a.status, {"ok", "quality_warning"}, result_a.error)
        self.assertIn(result_b.status, {"ok", "quality_warning"}, result_b.error)
        self.assertEqual(trace_a.first_usage.total_tokens, 112)
        self.assertEqual(trace_b.first_usage.total_tokens, 224)
        self.assertEqual(trace_a.retrieval_cache_hit_count, 11)
        self.assertEqual(trace_b.retrieval_cache_hit_count, 12)
        self.assertEqual(trace_a.subdirection_retrieval_query_count, 6)
        self.assertEqual(trace_b.subdirection_retrieval_query_count, 6)

    def test_six_fixed_domains_run_end_to_end_with_fakes(self) -> None:
        domains = [
            "多模态大模型",
            "多智能体辩论",
            "检索增强生成",
            "图神经网络",
            "扩散模型",
            "大模型幻觉检测",
        ]
        paper_ids = [paper.paper_id for paper in make_candidates()]
        config = DomainOnboardingConfig(enforce_core_paper_coverage=False)
        for domain in domains:
            with self.subTest(domain=domain):
                payload = make_generation_payload(paper_ids)
                payload["domain"] = domain
                result = make_pipeline([payload], config=config).run(
                    DomainOnboardingRequest(query=domain), DomainOnboardingRequestTrace()
                )
                self.assertEqual(result.status, "ok")
                expected_domain = (
                    "检索增强生成（RAG）" if domain == "检索增强生成" else domain
                )
                self.assertEqual(result.output.domain, expected_domain)

    def test_full_pipeline_succeeds_with_fake_retrieval_and_model(self) -> None:
        paper_ids = [paper.paper_id for paper in make_candidates()]
        pipeline = make_pipeline([make_generation_payload(paper_ids)])
        trace = DomainOnboardingRequestTrace()
        result = pipeline.run(DomainOnboardingRequest(query="RAG"), trace)
        self.assertEqual(result.status, "ok")
        self.assertIsNotNone(result.final_quality)
        self.assertEqual(result.final_quality.final_score, result.quality.score)
        self.assertEqual(result.to_response()["final_quality"]["verdict"], "passed")
        self.assertIsNotNone(result.output)
        self.assertTrue(result.output.papers)
        self.assertTrue(result.output.evidence_papers)
        self.assertTrue(
            all(
                paper.paper_usage in {"recommendation", "both"}
                for paper in result.output.papers
            )
        )
        self.assertEqual(len(result.quality_attempts), 1)
        self.assertEqual(result.quality_attempts[0].source, "initial")
        self.assertFalse(result.repair_record.triggered)
        self.assertEqual(
            result.repair_record.decision.decision,
            "initial_selected",
        )
        self.assertGreater(trace.retrieved_paper_count, 0)
        self.assertGreater(trace.selected_paper_count, 0)
        self.assertTrue(trace.ranking_role_candidate_counts)
        self.assertTrue(trace.ranking_selected_role_counts)
        self.assertEqual(
            result.output.reproducibility["ranking_selected_role_counts"],
            trace.ranking_selected_role_counts,
        )
        self.assertGreater(trace.supplemental_query_count, 0)
        self.assertEqual(
            trace.search_query_count,
            2
            + trace.supplemental_query_count
            + trace.subdirection_retrieval_query_count
            + trace.recommendation_survey_query_count,
        )
        self.assertEqual(pipeline.retriever.calls, 6)
        self.assertEqual(trace.subdirection_retrieval_query_count, 6)
        self.assertEqual(
            len(pipeline.retriever.query_batches[1]),
            trace.supplemental_query_count,
        )
        self.assertTrue(
            any('"检索"' in query for query in pipeline.retriever.query_batches[1])
        )

    def test_all_retrieval_failures_return_explicit_error_without_generation(self) -> None:
        pipeline = make_pipeline([make_generation_payload(["paper-0"])], fail_retrieval=True)
        trace = DomainOnboardingRequestTrace()
        result = pipeline.run(DomainOnboardingRequest(query="RAG"), trace)
        self.assertEqual(result.status, "retrieval_failed")
        self.assertIsNone(result.output)
        self.assertEqual(trace.retrieval_error_count, 1)

    def test_empty_verified_candidate_set_returns_retrieval_failed(self) -> None:
        pipeline = make_pipeline([{}], empty_retrieval=True)
        result = pipeline.run(
            DomainOnboardingRequest(query="RAG"), DomainOnboardingRequestTrace()
        )
        self.assertEqual(result.status, "retrieval_failed")

    def test_invalid_generation_json_returns_generation_failed(self) -> None:
        pipeline = make_pipeline(["not json"])
        trace = DomainOnboardingRequestTrace()
        result = pipeline.run(
            DomainOnboardingRequest(query="RAG"), trace
        )
        self.assertEqual(result.status, "generation_failed")
        self.assertEqual(trace.first_model_calls, 1)
        self.assertEqual(trace.first_usage.total_tokens, 50)
        self.assertEqual(trace.first_unreported_usage_calls, 0)

    def test_repair_is_selected_only_after_significant_improvement(self) -> None:
        paper_ids = [paper.paper_id for paper in make_candidates()]
        low = make_generation_payload(paper_ids)
        low["text"] = "太短"
        low["prerequisites"] = low["prerequisites"][:1]
        low["development_stages"] = low["development_stages"][:1]
        low["current_landscape"] = {"problems": ["一个问题"], "subdirections": ["一个方向"]}
        high = make_generation_payload(paper_ids)
        pipeline = make_pipeline([low, high])
        trace = DomainOnboardingRequestTrace()
        result = pipeline.run(DomainOnboardingRequest(query="RAG"), trace)
        self.assertEqual(result.quality.retry_status, "improved")
        self.assertEqual(result.quality.selected_attempt, 2)
        self.assertEqual(len(result.quality_attempts), 2)
        self.assertEqual(
            [attempt.source for attempt in result.quality_attempts],
            ["initial", "llm_repair"],
        )
        self.assertTrue(result.repair_record.triggered)
        self.assertEqual(
            result.repair_record.decision.decision,
            "repaired_selected",
        )
        self.assertLess(
            result.quality_attempts[0].quality.score,
            result.quality_attempts[1].quality.score,
        )
        self.assertGreater(trace.quality_delta, 0.05)
        self.assertEqual(trace.repair_reason, "llm_targeted_repair")
        self.assertEqual(pipeline.retriever.calls, 6)
        self.assertEqual(trace.subdirection_retrieval_query_count, 6)

    def test_adaptive_repair_recommendation_is_shadow_only_and_recorded(self) -> None:
        class FakeAdvisor:
            policy = SimpleNamespace(policy_version="domain-repair-adaptive-v1.0.0")

            def recommend(self, quality):
                return {quality.issues[0].issue_type: "code"}

        paper_ids = [paper.paper_id for paper in make_candidates()]
        low = make_generation_payload(paper_ids)
        low["text"] = "太短"
        low["development_stages"] = low["development_stages"][:1]
        pipeline = make_pipeline([low, low], repair_advisor=FakeAdvisor())
        trace = DomainOnboardingRequestTrace()

        result = pipeline.run(DomainOnboardingRequest(query="RAG"), trace)

        self.assertEqual(
            result.repair_record.adaptive_policy_version,
            "domain-repair-adaptive-v1.0.0",
        )
        self.assertTrue(result.repair_record.shadow_recommendations)
        self.assertEqual(trace.adaptive_recommendations, result.repair_record.shadow_recommendations)
        self.assertNotEqual(result.repair_record.actions, [])

    def test_unimproved_repair_keeps_first_result(self) -> None:
        paper_ids = [paper.paper_id for paper in make_candidates()]
        low = make_generation_payload(paper_ids)
        low["text"] = "太短"
        low["development_stages"] = low["development_stages"][:1]
        pipeline = make_pipeline([low, low])
        trace = DomainOnboardingRequestTrace()
        result = pipeline.run(DomainOnboardingRequest(query="RAG"), trace)
        self.assertEqual(result.status, "quality_warning")
        self.assertEqual(result.quality.retry_status, "not_improved")
        self.assertEqual(result.quality.selected_attempt, 1)
        self.assertEqual(len(result.quality_attempts), 2)
        self.assertEqual(result.quality_attempts[0].quality.score, result.quality.score)
        self.assertEqual(
            result.repair_record.decision.decision,
            "initial_retained",
        )
        self.assertIn(
            "improvement_too_small",
            result.repair_record.decision.reasons,
        )

    def test_cancelled_repair_preserves_completed_quality_history(self) -> None:
        paper_ids = [paper.paper_id for paper in make_candidates()]
        low = make_generation_payload(paper_ids)
        low["text"] = "太短"
        low["development_stages"] = low["development_stages"][:1]
        pipeline = make_pipeline([low, low])
        context = PipelineExecutionContext(timeout_seconds=30)
        original_repairer = pipeline.repairer

        class CancellingRepairer:
            def repair(self, *args, **kwargs):
                result = original_repairer.repair(*args, **kwargs)
                context.cancel()
                return result

        pipeline.repairer = CancellingRepairer()

        result = pipeline.run(
            DomainOnboardingRequest(query="RAG"),
            DomainOnboardingRequestTrace(),
            context,
        )

        self.assertEqual(result.status, "cancelled")
        self.assertEqual(len(result.quality_attempts), 1)
        self.assertEqual(result.quality_attempts[0].source, "initial")
        self.assertIsNotNone(result.quality)

    def test_soft_quality_shortfall_returns_warning_instead_of_ok(self) -> None:
        paper_ids = [paper.paper_id for paper in make_candidates()]
        low = make_generation_payload(paper_ids)
        for stage in low["development_stages"]:
            stage.update(
                summary="",
                motivation="",
                core_concepts=[],
                main_techniques=[],
                open_problems=[],
            )
        for step in low["learning_path"]:
            step.update(topics=[], activities=[], completion_criteria=[])
        pipeline = make_pipeline(
            [low, low],
            config=DomainOnboardingConfig(quality_threshold=0.95),
        )

        result = pipeline.run(
            DomainOnboardingRequest(query="RAG"),
            DomainOnboardingRequestTrace(),
        )

        self.assertTrue(result.quality.passed_hard_gates)
        self.assertLess(result.quality.score, result.quality.threshold)
        self.assertEqual(result.status, "quality_warning")

    def test_strict_quality_gate_mode_remains_available_for_offline_audits(self) -> None:
        paper_ids = [paper.paper_id for paper in make_candidates()]
        low = make_generation_payload(paper_ids)
        low["text"] = "太短"
        low["development_stages"] = low["development_stages"][:1]
        pipeline = make_pipeline(
            [low, low],
            config=DomainOnboardingConfig(quality_gate_enforcement="strict"),
        )

        result = pipeline.run(
            DomainOnboardingRequest(query="RAG"),
            DomainOnboardingRequestTrace(),
        )

        self.assertEqual(result.status, "quality_failed")
        self.assertFalse(result.quality.passed_hard_gates)

    def test_repair_timeout_delivers_initial_output_as_quality_warning(self) -> None:
        now = [0.0]
        paper_ids = [paper.paper_id for paper in make_candidates()]
        low = make_generation_payload(paper_ids)
        low["text"] = "太短"
        low["development_stages"] = low["development_stages"][:1]
        pipeline = make_pipeline([low, low])
        original_repairer = pipeline.repairer

        class SlowRepairer:
            def repair(self, *args, **kwargs):
                result = original_repairer.repair(*args, **kwargs)
                now[0] += pipeline.config.repair_timeout_seconds + 1.0
                return result

        pipeline.repairer = SlowRepairer()
        trace = DomainOnboardingRequestTrace()
        context = PipelineExecutionContext(
            timeout_seconds=pipeline.config.request_timeout_seconds,
            clock=lambda: now[0],
        )

        result = pipeline.run(
            DomainOnboardingRequest(query="RAG"),
            trace,
            context,
        )

        self.assertEqual(result.status, "quality_warning")
        self.assertIsNotNone(result.output)
        self.assertEqual(result.quality.retry_status, "llm_failed")
        self.assertEqual(trace.interrupted_stage, "repair")
        self.assertTrue(trace.deadline_exceeded)
        self.assertIn("deadline exceeded during repair", result.error)

    def test_improved_result_must_meet_threshold_to_return_ok(self) -> None:
        paper_ids = [paper.paper_id for paper in make_candidates()]
        low = make_generation_payload(paper_ids)
        low["text"] = "太短"
        low["prerequisites"] = low["prerequisites"][:1]
        low["development_stages"] = low["development_stages"][:1]
        low["current_landscape"] = {"problems": ["一个问题"], "subdirections": ["一个方向"]}
        high = make_generation_payload(paper_ids)
        pipeline = make_pipeline([low, high])

        result = pipeline.run(
            DomainOnboardingRequest(query="RAG"),
            DomainOnboardingRequestTrace(),
        )

        self.assertEqual(result.status, "ok")
        self.assertGreaterEqual(result.quality.score, result.quality.threshold)

    def test_failed_llm_repair_records_retry_usage(self) -> None:
        paper_ids = [paper.paper_id for paper in make_candidates()]
        low = make_generation_payload(paper_ids)
        low["text"] = "太短"
        low["development_stages"] = low["development_stages"][:1]
        pipeline = make_pipeline([low, "not json"])
        trace = DomainOnboardingRequestTrace()

        result = pipeline.run(DomainOnboardingRequest(query="RAG"), trace)

        self.assertEqual(result.status, "quality_warning")
        self.assertEqual(trace.repair_reason, "llm_repair_failed")
        self.assertEqual(trace.retry_model_calls, 1)
        self.assertEqual(trace.retry_usage.total_tokens, 50)
        self.assertEqual(trace.retry_unreported_usage_calls, 0)
        self.assertIn(
            "repair_execution_failed",
            result.repair_record.decision.reasons,
        )


class HandlerAndMetricsTests(unittest.TestCase):
    def test_metrics_aggregate_quality_and_repair_audit_fields(self) -> None:
        metrics = DomainOnboardingMetrics()
        metrics.record(
            DomainOnboardingRequestTrace(
                status="quality_warning",
                retry_status="not_improved",
                repair_reason="llm_targeted_repair",
                first_quality_state="failed",
                final_quality_state="warning",
                first_issue_type_counts={"structure_error": 1, "missing_coverage": 2},
                hard_gate_failure_counts={"required_structure": 1},
                repair_action_status_counts={
                    "code:applied": 1,
                    "llm:failed": 1,
                },
                repair_selection_reasons=[
                    "improvement_too_small",
                    "repair_execution_failed",
                ],
                repair_changed_path_count=3,
                repair_dimension_deltas={"structure": 0.2},
                adaptive_policy_version="domain-repair-adaptive-v1.0.0",
                adaptive_recommendations={"route_conflict": "code"},
            )
        )

        snapshot = metrics.snapshot()

        self.assertEqual(snapshot["quality"]["first_states"], {"failed": 1})
        self.assertEqual(snapshot["quality"]["final_states"], {"warning": 1})
        self.assertEqual(snapshot["quality"]["issue_types"]["missing_coverage"], 2)
        self.assertEqual(
            snapshot["quality"]["hard_gate_failures"],
            {"required_structure": 1},
        )
        self.assertEqual(snapshot["repair"]["actions"]["llm:failed"], 1)
        self.assertEqual(
            snapshot["repair"]["selection_reasons"]["repair_execution_failed"],
            1,
        )
        self.assertEqual(snapshot["repair"]["changed_paths"]["total"], 3)
        self.assertEqual(snapshot["repair"]["dimension_deltas"]["structure"], 0.2)
        self.assertEqual(
            snapshot["adaptive_repair"]["recommendations"],
            {"route_conflict:code": 1},
        )

    def test_metrics_aggregate_timeout_and_interrupted_stage(self) -> None:
        metrics = DomainOnboardingMetrics()
        metrics.record(
            DomainOnboardingRequestTrace(
                status="timeout",
                interrupted_stage="retrieval",
                deadline_exceeded=True,
            )
        )

        snapshot = metrics.snapshot()

        self.assertEqual(snapshot["interruptions"]["timeouts"], 1)
        self.assertEqual(snapshot["interruptions"]["cancelled"], 0)
        self.assertEqual(snapshot["interruptions"]["stages"], {"retrieval": 1})

    def test_metrics_aggregate_provider_health(self) -> None:
        metrics = DomainOnboardingMetrics()
        metrics.record(
            DomainOnboardingRequestTrace(
                status="ok",
                retrieval_provider_stats={
                    "arxiv": {
                        "success": True,
                        "latency_ms": 12.5,
                        "result_count": 4,
                        "error_count": 0,
                        "rate_limit_count": 1,
                        "circuit_open": False,
                        "stale_cache_used": False,
                    }
                },
            )
        )

        provider = metrics.snapshot()["retrieval_providers"]["arxiv"]

        self.assertEqual(provider["success"], 1)
        self.assertEqual(provider["result_count"], 4)
        self.assertEqual(provider["rate_limit_count"], 1)
        self.assertEqual(provider["latency"]["average_ms"], 12.5)

    def test_handler_builds_request_from_channel_metadata_and_records_metrics(self) -> None:
        paper_ids = [paper.paper_id for paper in make_candidates()]
        metrics = DomainOnboardingMetrics()
        app_state = SimpleNamespace(
            domain_onboarding_pipeline=make_pipeline([make_generation_payload(paper_ids)]),
            domain_onboarding_metrics=metrics,
        )
        message = ChannelMessage(
            session_id="session",
            user_id="user",
            channel="test",
            direction="inbound",
            mode="domain_onboarding",
            content="RAG",
            metadata={"preference": "experiment_first", "time_budget_weeks": 6},
        )
        response = handle_domain_onboarding_message(message, app_state)
        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["learner_profile"]["preference"], "balanced")
        self.assertIsNone(response["learner_profile"]["time_budget_weeks"])
        self.assertEqual(len(response["quality_attempts"]), 1)
        self.assertEqual(response["quality_attempts"][0]["source"], "initial")
        self.assertFalse(response["repair_record"]["triggered"])
        self.assertEqual(
            response["repair_record"]["decision"]["decision"],
            "initial_selected",
        )
        snapshot = metrics.snapshot()
        self.assertEqual(snapshot["requests_total"], 1)
        self.assertEqual(snapshot["papers"]["selected_paper_count"], len(response["papers"]))
        self.assertIn("retrieval_retry_count", snapshot["papers"])
        self.assertIn("retrieval_cache_hit_count", snapshot["papers"])
        self.assertIn("recommendation_survey_query_count", snapshot["papers"])
        self.assertIn("recommendation_selected_survey_count", snapshot["papers"])
        self.assertIn("recommendation_degraded_count", snapshot["papers"])
        self.assertIn("retrieval_source_failure_count", snapshot["papers"])
        self.assertIn("planning", snapshot["stage_latency"])
        self.assertGreaterEqual(
            snapshot["ranking"]["vectorizer_backends"]["multilingual_tfidf"],
            1,
        )
        self.assertGreater(snapshot["evidence"]["evidence_claim_count"], 0)
        self.assertEqual(snapshot["quality"]["final_states"]["passed"], 1)
        self.assertEqual(
            snapshot["repair"]["selection_reasons"]["quality_threshold_met"],
            1,
        )
        self.assertEqual(
            snapshot["policies"]["versions"],
            {"domain-quality-v1.8.0": 1},
        )
        self.assertEqual(response["policy_version"], "domain-quality-v1.8.0")
        self.assertEqual(
            response["quality"]["policy_fingerprint"],
            response["policy_fingerprint"],
        )
        self.assertEqual(
            response["repair_record"]["policy_version"],
            response["policy_version"],
        )

    def test_custom_policy_version_propagates_through_result_and_trace(self) -> None:
        paper_ids = [paper.paper_id for paper in make_candidates()]
        config = DomainOnboardingConfig(policy_version="domain-quality-v1.1.0")
        trace = DomainOnboardingRequestTrace()

        result = make_pipeline(
            [make_generation_payload(paper_ids)],
            config=config,
        ).run(DomainOnboardingRequest(query="RAG"), trace)

        self.assertEqual(result.policy_version, "domain-quality-v1.1.0")
        self.assertEqual(result.quality.policy_version, result.policy_version)
        self.assertEqual(result.repair_record.policy_version, result.policy_version)
        self.assertEqual(trace.policy_version, result.policy_version)
        self.assertEqual(trace.policy_fingerprint, result.policy_fingerprint)

    def test_handler_preserves_quality_warning_and_records_status(self) -> None:
        paper_ids = [paper.paper_id for paper in make_candidates()]
        low = make_generation_payload(paper_ids)
        for stage in low["development_stages"]:
            stage.update(
                summary="",
                motivation="",
                core_concepts=[],
                main_techniques=[],
                open_problems=[],
            )
        metrics = DomainOnboardingMetrics()
        app_state = SimpleNamespace(
            domain_onboarding_pipeline=make_pipeline(
                [low, low],
                config=DomainOnboardingConfig(quality_threshold=0.95),
            ),
            domain_onboarding_metrics=metrics,
        )
        message = ChannelMessage(
            session_id="session",
            channel="test",
            direction="inbound",
            mode="domain_onboarding",
            content="RAG",
        )

        response = handle_domain_onboarding_message(message, app_state)

        self.assertEqual(response["status"], "quality_warning")
        self.assertIn("quality", response)
        self.assertTrue(response["papers"])
        self.assertEqual(metrics.snapshot()["statuses"]["quality_warning"], 1)
        self.assertTrue(metrics.snapshot()["repair"]["actions"])

    def test_empty_input_is_recorded(self) -> None:
        metrics = DomainOnboardingMetrics()
        app_state = SimpleNamespace(
            domain_onboarding_pipeline=make_pipeline([{}]),
            domain_onboarding_metrics=metrics,
        )
        message = ChannelMessage(
            session_id="session",
            channel="test",
            direction="inbound",
            mode="domain_onboarding",
            content="   ",
        )
        response = handle_domain_onboarding_message(message, app_state)
        self.assertEqual(response["status"], "invalid_input")
        self.assertEqual(metrics.snapshot()["statuses"]["invalid_input"], 1)

    def test_failed_generation_usage_is_aggregated(self) -> None:
        metrics = DomainOnboardingMetrics()
        app_state = SimpleNamespace(
            domain_onboarding_pipeline=make_pipeline(["not json"]),
            domain_onboarding_metrics=metrics,
        )
        message = ChannelMessage(
            session_id="session",
            channel="test",
            direction="inbound",
            mode="domain_onboarding",
            content="RAG",
        )

        response = handle_domain_onboarding_message(message, app_state)
        usage = metrics.snapshot()["model_usage"]

        self.assertEqual(response["status"], "generation_failed")
        self.assertEqual(usage["primary"]["model_calls"], 1)
        self.assertEqual(usage["primary"]["total_tokens"], 50)
        self.assertTrue(usage["primary"]["usage_complete"])

    def test_unreported_failed_call_is_visible_in_metrics(self) -> None:
        metrics = DomainOnboardingMetrics()
        app_state = SimpleNamespace(
            domain_onboarding_pipeline=make_pipeline([RuntimeError("offline")]),
            domain_onboarding_metrics=metrics,
        )
        message = ChannelMessage(
            session_id="session",
            channel="test",
            direction="inbound",
            mode="domain_onboarding",
            content="RAG",
        )

        response = handle_domain_onboarding_message(message, app_state)
        primary = metrics.snapshot()["model_usage"]["primary"]

        self.assertEqual(response["status"], "generation_failed")
        self.assertEqual(primary["model_calls"], 1)
        self.assertEqual(primary["unreported_usage_calls"], 1)
        self.assertFalse(primary["usage_complete"])


if __name__ == "__main__":
    unittest.main()
