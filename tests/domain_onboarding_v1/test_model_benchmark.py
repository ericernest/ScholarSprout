from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from evaluation.domain_onboarding.model_benchmark import (
    FrozenPlanner,
    ModelBenchmarkRun,
    build_schedule,
    is_resumable_complete,
    is_transient_infrastructure_failure,
    load_completed_runs,
    run_benchmark_case,
    summarize_runs,
    write_benchmark_outputs,
)
from tests.domain_onboarding_v1.fakes import make_plan
from evaluation.domain_onboarding.online import OnlineEvaluationCase
from handlers.domain_onboarding.schemas import (
    ContentQuality,
    CurrentLandscape,
    DomainOnboardingOutput,
    LearnerProfile,
    PipelineResult,
    QualityAttempt,
    RepairRecord,
    SelectedPaper,
)
from runtime.agent_runner import TokenUsage


def make_case(case_id: str = "rag-zh") -> OnlineEvaluationCase:
    return OnlineEvaluationCase(
        case_id=case_id,
        domain="检索增强生成",
        language="zh",
        query="我想学习检索增强生成 RAG",
    )


class FakeBenchmarkPipeline:
    def run(self, request, trace, progress_callback=None):
        trace.first_model_calls = 3
        trace.retry_model_calls = 1
        trace.first_usage = TokenUsage(1000, 400, 1400, True)
        trace.retry_usage = TokenUsage(200, 100, 300, True)
        trace.planning_duration_ms = 100.0
        trace.generation_duration_ms = 500.0
        if progress_callback is not None:
            progress_callback("llm_delta", 0.18, True, [], {"delta": "{"})
            progress_callback("development_ready", 0.68, True, [], {})
        quality = ContentQuality(
            score=0.81,
            threshold=0.75,
            passed_hard_gates=True,
            dimensions={"structure": 0.9},
        )
        output = DomainOnboardingOutput(
            domain=request.query,
            text="A sufficiently long deterministic benchmark output summary.",
            learner_profile=LearnerProfile(),
            prerequisites=[],
            development_stages=[],
            current_landscape=CurrentLandscape(),
            learning_path=[],
            papers=[
                SelectedPaper(
                    paper_id="paper-1",
                    title="Valid RAG Paper",
                    year=2024,
                    url="https://example.org/paper-1",
                    source="test",
                )
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
            repair_record=RepairRecord(triggered=False),
        )


class ModelBenchmarkTests(unittest.TestCase):
    def test_frozen_planner_returns_a_deep_copy(self) -> None:
        source = make_plan()
        planner = FrozenPlanner(source)

        first = planner.plan("ignored")
        first.plan.search_queries.append("mutated")
        second = planner.plan("ignored")

        self.assertNotIn("mutated", source.search_queries)
        self.assertNotIn("mutated", second.plan.search_queries)

    def test_schedule_has_each_mode_model_case_and_repeat_once(self) -> None:
        cases = [make_case("rag-zh"), make_case("multi-agent-debate-zh")]
        schedule = build_schedule(
            modes=["end_to_end", "model_only"],
            models=["model-a", "model-b", "model-c"],
            cases=cases,
            repeats=2,
            seed=7,
        )

        self.assertEqual(len(schedule), 24)
        keys = {
            (mode, model, case.case_id, repeat)
            for mode, model, case, repeat in schedule
        }
        self.assertEqual(len(keys), 24)

    def test_run_captures_tokens_latency_quality_and_visible_progress(self) -> None:
        result = run_benchmark_case(
            FakeBenchmarkPipeline(),
            mode="end_to_end",
            model="model-a",
            case=make_case(),
            repeat=1,
        )

        self.assertEqual(result.status, "ok")
        self.assertTrue(result.generation_succeeded)
        self.assertTrue(result.delivery_succeeded)
        self.assertEqual(result.model_calls, 4)
        self.assertEqual(result.repair_model_calls, 1)
        self.assertEqual(result.prompt_tokens, 1200)
        self.assertEqual(result.completion_tokens, 500)
        self.assertEqual(result.total_tokens, 1700)
        self.assertTrue(result.usage_complete)
        self.assertIsNotNone(result.first_llm_delta_ms)
        self.assertIsNotNone(result.first_visible_section_ms)
        self.assertEqual(result.quality_score, 0.81)
        self.assertTrue(result.hard_gate_passed)
        self.assertFalse(result.repair_triggered)

    def test_summary_and_csv_outputs_group_by_mode_and_model(self) -> None:
        base = ModelBenchmarkRun(
            run_key="end_to_end:model-a:rag-zh:r1",
            mode="end_to_end",
            model="model-a",
            case_id="rag-zh",
            domain="检索增强生成",
            repeat=1,
            status="ok",
            generation_succeeded=True,
            delivery_succeeded=True,
            duration_ms=1000,
            first_llm_delta_ms=100,
            first_visible_section_ms=700,
            generation_ms=800,
            model_calls=3,
            prompt_tokens=1000,
            completion_tokens=500,
            total_tokens=1500,
            usage_complete=True,
            quality_score=0.8,
            hard_gate_passed=True,
        )
        second = base.model_copy(
            update={
                "run_key": "end_to_end:model-a:rag-zh:r2",
                "repeat": 2,
                "duration_ms": 2000,
                "total_tokens": 2500,
            }
        )

        summaries = summarize_runs([base, second])

        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].runs, 2)
        self.assertEqual(summaries[0].p50_duration_ms, 1500)
        self.assertEqual(summaries[0].p95_duration_ms, 2000)
        self.assertEqual(summaries[0].total_tokens, 4000)
        with tempfile.TemporaryDirectory() as directory:
            details, summary, report = write_benchmark_outputs(
                directory, [base, second]
            )
            self.assertTrue(details.is_file())
            self.assertTrue(summary.is_file())
            self.assertTrue(report.is_file())
            raw = Path(directory) / "runs.jsonl"
            stale = base.model_copy(update={"total_tokens": 0, "usage_complete": False})
            raw.write_text(
                stale.model_dump_json() + "\n" + base.model_dump_json() + "\n",
                encoding="utf-8",
            )
            self.assertEqual(load_completed_runs(raw), [base])
            self.assertTrue(is_resumable_complete(base))
            self.assertFalse(is_resumable_complete(stale))

    def test_millisecond_zero_token_connection_failure_is_resumable(self) -> None:
        transient = ModelBenchmarkRun(
            run_key="model_only:model-a:rag-zh:r1",
            mode="model_only",
            model="model-a",
            case_id="rag-zh",
            domain="检索增强生成",
            repeat=1,
            status="generation_failed",
            generation_succeeded=False,
            delivery_succeeded=False,
            error="development section generation failed: Connection error.",
            duration_ms=5.0,
        )
        provider_timeout = transient.model_copy(
            update={"error": "The read operation timed out", "duration_ms": 380_000}
        )
        throttled = transient.model_copy(
            update={
                "error": "Error code: 429 - rate limit exceeded (throttling_error)",
                "duration_ms": 40_000,
                "total_tokens": 1200,
            }
        )
        ranking_timeout = transient.model_copy(
            update={
                "status": "timeout",
                "error": "domain onboarding deadline exceeded during ranking",
                "duration_ms": 280_000,
                "total_tokens": 1200,
                "generation_ms": 0.0,
            }
        )

        self.assertTrue(is_transient_infrastructure_failure(transient))
        self.assertFalse(is_resumable_complete(transient))
        self.assertFalse(is_transient_infrastructure_failure(provider_timeout))
        self.assertTrue(is_resumable_complete(provider_timeout))
        self.assertTrue(is_transient_infrastructure_failure(throttled))
        self.assertFalse(is_resumable_complete(throttled))
        self.assertTrue(is_transient_infrastructure_failure(ranking_timeout))
        self.assertFalse(is_resumable_complete(ranking_timeout))


if __name__ == "__main__":
    unittest.main()
