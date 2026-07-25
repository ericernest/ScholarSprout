from __future__ import annotations

import unittest
from types import SimpleNamespace

from channels.base import ChannelMessage
from handlers.domain_onboarding.config import DomainOnboardingConfig
from handlers.domain_onboarding.generator import StructuredOnboardingGenerator
from handlers.domain_onboarding.metrics import DomainOnboardingMetrics, DomainOnboardingRequestTrace
from handlers.domain_onboarding.pipeline import DomainOnboardingPipeline
from handlers.domain_onboarding.profile import RuleBasedProfileBuilder
from handlers.domain_onboarding.quality import CompositeQualityEvaluator
from handlers.domain_onboarding.ranking import WeightedPaperRanker
from handlers.domain_onboarding.repair import TargetedRepairer
from handlers.domain_onboarding.retrieval import PaperRetrievalError
from handlers.domain_onboarding.schemas import DomainOnboardingRequest, ModelCallStats
from handlers.domain_onboarding_handler import handle_domain_onboarding_message

from .fakes import FakeJSONModel, make_candidates, make_generation_payload, make_plan


class FakePlanner:
    def __init__(self) -> None:
        self.last_stats = ModelCallStats()

    def plan(self, query: str, profile: object) -> object:
        return make_plan()


class FakeRetriever:
    def __init__(self, *, fail: bool = False, empty: bool = False) -> None:
        self.fail = fail
        self.empty = empty
        self.last_errors: list[str] = []
        self.calls = 0

    def search(self, queries: list[str], *, limit_per_query: int) -> list[object]:
        self.calls += 1
        if self.fail:
            self.last_errors = ["offline"]
            raise PaperRetrievalError("all paper queries failed")
        return [] if self.empty else make_candidates()


def make_pipeline(
    responses: list[object],
    *,
    fail_retrieval: bool = False,
    empty_retrieval: bool = False,
    config: DomainOnboardingConfig | None = None,
) -> DomainOnboardingPipeline:
    config = config or DomainOnboardingConfig()
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
    )


class PipelineTests(unittest.TestCase):
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
        for domain in domains:
            with self.subTest(domain=domain):
                payload = make_generation_payload(paper_ids)
                payload["domain"] = domain
                result = make_pipeline([payload]).run(
                    DomainOnboardingRequest(query=domain), DomainOnboardingRequestTrace()
                )
                self.assertEqual(result.status, "ok")
                self.assertEqual(result.output.domain, domain)

    def test_full_pipeline_succeeds_with_fake_retrieval_and_model(self) -> None:
        paper_ids = [paper.paper_id for paper in make_candidates()]
        pipeline = make_pipeline([make_generation_payload(paper_ids)])
        trace = DomainOnboardingRequestTrace()
        result = pipeline.run(DomainOnboardingRequest(query="RAG"), trace)
        self.assertEqual(result.status, "ok")
        self.assertIsNotNone(result.output)
        self.assertGreater(trace.retrieved_paper_count, 0)
        self.assertGreater(trace.selected_paper_count, 0)
        self.assertEqual(trace.search_query_count, 2)

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
        result = pipeline.run(
            DomainOnboardingRequest(query="RAG"), DomainOnboardingRequestTrace()
        )
        self.assertEqual(result.status, "generation_failed")

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
        self.assertGreater(trace.quality_delta, 0.05)
        self.assertEqual(trace.repair_reason, "llm_targeted_repair")
        self.assertEqual(pipeline.retriever.calls, 2)

    def test_unimproved_repair_keeps_first_result(self) -> None:
        paper_ids = [paper.paper_id for paper in make_candidates()]
        low = make_generation_payload(paper_ids)
        low["text"] = "太短"
        low["development_stages"] = low["development_stages"][:1]
        pipeline = make_pipeline([low, low])
        trace = DomainOnboardingRequestTrace()
        result = pipeline.run(DomainOnboardingRequest(query="RAG"), trace)
        self.assertEqual(result.status, "quality_failed")
        self.assertEqual(result.quality.retry_status, "not_improved")
        self.assertEqual(result.quality.selected_attempt, 1)

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


class HandlerAndMetricsTests(unittest.TestCase):
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
        self.assertEqual(response["learner_profile"]["preference"], "experiment_first")
        snapshot = metrics.snapshot()
        self.assertEqual(snapshot["requests_total"], 1)
        self.assertEqual(snapshot["papers"]["selected_paper_count"], len(response["papers"]))
        self.assertIn("retrieval_retry_count", snapshot["papers"])
        self.assertIn("retrieval_cache_hit_count", snapshot["papers"])
        self.assertIn("retrieval_source_failure_count", snapshot["papers"])
        self.assertIn("planning", snapshot["stage_latency"])

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


if __name__ == "__main__":
    unittest.main()
