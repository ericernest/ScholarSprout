"""编排领域入门 V1 的画像、规划、检索、排序、生成、评估与修复。"""

from __future__ import annotations

import os
from typing import Any

from runtime.agent_runner import TokenUsage

from .config import DomainOnboardingConfig
from .execution import PipelineExecutionContext, PipelineExecutionHalted
from .generator import GenerationError, StructuredOnboardingGenerator
from .metrics import DomainOnboardingRequestTrace
from .planner import StormLitePlanner
from .profile import RuleBasedProfileBuilder
from .quality import CompositeQualityEvaluator, critical_dimensions_not_regressed
from .ranking import WeightedPaperRanker
from .repair import TargetedRepairer
from .retrieval import (
    ArxivRetriever,
    CompositePaperRetriever,
    CrossrefRetriever,
    PaperRetrievalError,
    SemanticScholarRetriever,
)
from .retrieval_resilience import RetrievalRetryPolicy
from .schemas import (
    ContentQuality,
    DomainOnboardingRequest,
    ModelCallStats,
    PipelineResult,
    RetrievalStats,
)


class DomainOnboardingPipeline:
    def __init__(
        self,
        *,
        profile_builder: Any,
        planner: Any,
        retriever: Any,
        ranker: Any,
        generator: Any,
        evaluator: Any,
        repairer: Any,
        config: DomainOnboardingConfig,
    ) -> None:
        self.profile_builder = profile_builder
        self.planner = planner
        self.retriever = retriever
        self.ranker = ranker
        self.generator = generator
        self.evaluator = evaluator
        self.repairer = repairer
        self.config = config

    def run(
        self,
        request: DomainOnboardingRequest,
        trace: DomainOnboardingRequestTrace,
        execution_context: PipelineExecutionContext | None = None,
    ) -> PipelineResult:
        context = execution_context or PipelineExecutionContext(
            timeout_seconds=self.config.request_timeout_seconds
        )
        partial_output = None
        partial_quality = None
        try:
            profile, trace.profile_duration_ms = context.call(
                "profile",
                self.config.profile_timeout_seconds,
                self.profile_builder.build,
                request,
            )
            try:
                planning_result, trace.planning_duration_ms = context.call(
                    "planning",
                    self.config.planning_timeout_seconds,
                    self.planner.plan,
                    request.query,
                    profile,
                )
            except Exception as error:
                if isinstance(error, PipelineExecutionHalted):
                    raise
                trace.planning_duration_ms = context.stage_durations_ms.get("planning", 0.0)
                return PipelineResult(status="planning_failed", query=request.query, error=str(error))
            plan = planning_result.plan
            self._record_planning_model_stats(trace, planning_result.stats)
            trace.search_query_count = len(plan.search_queries)

            try:
                retrieval_result, trace.retrieval_duration_ms = context.call(
                    "retrieval",
                    self.config.retrieval_stage_timeout_seconds,
                    self.retriever.search,
                    plan.search_queries,
                    limit_per_query=self.config.papers_per_query,
                )
            except PaperRetrievalError as error:
                trace.retrieval_duration_ms = context.stage_durations_ms.get("retrieval", 0.0)
                self._record_retrieval_stats(trace, error.stats)
                return PipelineResult(status="retrieval_failed", query=request.query, error=str(error))

            candidates = retrieval_result.papers
            trace.retrieved_paper_count = len(candidates)
            self._record_retrieval_stats(trace, retrieval_result.stats)
            ranking_result, trace.ranking_duration_ms = context.call(
                "ranking",
                self.config.ranking_timeout_seconds,
                self.ranker.rank,
                candidates,
                plan,
                limit=self.config.selected_paper_limit,
            )
            ranked = ranking_result.papers
            trace.deduplicated_paper_count = ranking_result.stats.deduplicated_count
            trace.invalid_paper_count = ranking_result.stats.invalid_count
            trace.verified_paper_count = max(0, trace.deduplicated_paper_count - trace.invalid_paper_count)
            trace.selected_paper_count = len(ranked)
            if not ranked:
                return PipelineResult(
                    status="retrieval_failed",
                    query=request.query,
                    error="No verified papers were returned by the configured data source.",
                )

            try:
                generation_result, trace.generation_duration_ms = context.call(
                    "generation",
                    self.config.generation_timeout_seconds,
                    self.generator.generate,
                    request,
                    profile,
                    plan,
                    ranked,
                )
                output = generation_result.output
            except GenerationError as error:
                trace.generation_duration_ms = context.stage_durations_ms.get("generation", 0.0)
                self._record_generation_model_stats(trace, error.stats)
                return PipelineResult(status="generation_failed", query=request.query, error=str(error))
            self._record_generation_model_stats(trace, generation_result.stats)
            partial_output = output

            first_quality, trace.evaluation_duration_ms = context.call(
                "evaluation",
                self.config.evaluation_timeout_seconds,
                self.evaluator.evaluate,
                output,
                ranked,
            )
            partial_quality = first_quality
            self._record_first(trace, first_quality)
            if first_quality.passed_hard_gates and first_quality.score >= first_quality.threshold:
                trace.final_score = first_quality.score
                trace.final_dimensions = dict(first_quality.dimensions)
                return PipelineResult(status="ok", query=request.query, output=output, quality=first_quality)

            if (
                any(issue.issue_type == "missing_coverage" for issue in first_quality.issues)
                and len(ranked) < self.config.selected_paper_limit
            ):
                ranked = self._supplement_papers(
                    plan, candidates, ranked, trace, context
                )

            repair_result, trace.repair_duration_ms = context.call(
                "repair",
                self.config.repair_timeout_seconds,
                self.repairer.repair,
                request,
                profile,
                plan,
                output,
                first_quality,
                ranked,
            )
            repaired = repair_result.output
            trace.repair_reason = repair_result.action
            self._record_retry_model_stats(trace, repair_result.stats)
            retry_quality, extra_eval_ms = context.call(
                "evaluation",
                self.config.evaluation_timeout_seconds,
                self.evaluator.evaluate,
                repaired,
                ranked,
            )
            trace.evaluation_duration_ms += extra_eval_ms

            use_retry = (
                retry_quality.passed_hard_gates
                and retry_quality.score >= first_quality.score + self.config.min_improvement_delta
                and critical_dimensions_not_regressed(first_quality, retry_quality)
            )
            if use_retry:
                retry_quality = self._with_retry(retry_quality, selected=2, status="improved")
                trace.retry_status = "improved"
                selected_output = repaired
                selected_quality = retry_quality
            else:
                first_quality = self._with_retry(first_quality, selected=1, status="not_improved")
                trace.retry_status = "not_improved"
                selected_output = output
                selected_quality = first_quality
            trace.final_score = selected_quality.score
            trace.final_dimensions = dict(selected_quality.dimensions)
            trace.quality_delta = round(selected_quality.score - float(trace.first_score or 0.0), 6)
            return PipelineResult(
                status=self._quality_status(selected_quality),
                query=request.query,
                output=selected_output,
                quality=selected_quality,
            )
        except PipelineExecutionHalted as error:
            trace.interrupted_stage = error.stage
            trace.deadline_exceeded = error.status == "timeout"
            trace.cancelled = error.status == "cancelled"
            duration_field = f"{error.stage}_duration_ms"
            if hasattr(trace, duration_field) and error.duration_ms > 0:
                setattr(
                    trace,
                    duration_field,
                    float(getattr(trace, duration_field)) + error.duration_ms,
                )
            return PipelineResult(
                status=error.status,
                query=request.query,
                output=partial_output,
                quality=partial_quality,
                error=str(error),
            )
        except Exception as error:
            return PipelineResult(status="internal_error", query=request.query, error=str(error))

    def close(self) -> None:
        close = getattr(self.retriever, "close", None)
        if callable(close):
            close()

    def _supplement_papers(
        self,
        plan: Any,
        candidates: list[Any],
        ranked: list[Any],
        trace: DomainOnboardingRequestTrace,
        context: PipelineExecutionContext,
    ) -> list[Any]:
        queries = [
            f'"{plan.normalized_domain}" "{subdirection}" survey method evaluation'
            for subdirection in plan.expected_subdirections
        ][: self.config.search_queries_limit]
        if not queries:
            return ranked
        try:
            retrieval_result, duration = context.call(
                "retrieval",
                self.config.retrieval_stage_timeout_seconds,
                self.retriever.search,
                queries,
                limit_per_query=self.config.papers_per_query,
            )
        except PaperRetrievalError as error:
            trace.retrieval_duration_ms = context.stage_durations_ms.get("retrieval", 0.0)
            self._record_retrieval_stats(trace, error.stats, accumulate=True)
            return ranked
        extra = retrieval_result.papers
        trace.retrieval_duration_ms += duration
        self._record_retrieval_stats(trace, retrieval_result.stats, accumulate=True)
        trace.search_query_count += len(queries)
        trace.retrieved_paper_count += len(extra)
        ranking_result, duration = context.call(
            "ranking",
            self.config.ranking_timeout_seconds,
            self.ranker.rank,
            [*candidates, *extra],
            plan,
            limit=self.config.selected_paper_limit,
        )
        trace.ranking_duration_ms += duration
        reranked = ranking_result.papers
        trace.deduplicated_paper_count = ranking_result.stats.deduplicated_count
        trace.invalid_paper_count = ranking_result.stats.invalid_count
        trace.verified_paper_count = max(0, trace.deduplicated_paper_count - trace.invalid_paper_count)
        trace.selected_paper_count = len(reranked)
        return reranked or ranked

    @staticmethod
    def _with_retry(quality: ContentQuality, *, selected: int, status: str) -> ContentQuality:
        return quality.model_copy(
            update={"attempts": 2, "selected_attempt": selected, "retry_status": status}
        )

    @staticmethod
    def _quality_status(quality: ContentQuality) -> str:
        if not quality.passed_hard_gates:
            return "quality_failed"
        if quality.score < quality.threshold:
            return "quality_warning"
        return "ok"

    @staticmethod
    def _usage_from_stats(stats: ModelCallStats) -> TokenUsage:
        return TokenUsage(
            prompt_tokens=stats.prompt_tokens,
            completion_tokens=stats.completion_tokens,
            total_tokens=stats.total_tokens,
            reported=stats.usage_reported,
        )

    def _record_planning_model_stats(
        self,
        trace: DomainOnboardingRequestTrace,
        planning: ModelCallStats,
    ) -> None:
        trace.first_model_calls += planning.model_calls
        trace.first_usage = self._usage_from_stats(planning)
        trace.first_call_duration_ms += planning.duration_ms
        if planning.model_calls and not planning.usage_reported:
            trace.first_unreported_usage_calls += planning.model_calls

    def _record_generation_model_stats(
        self,
        trace: DomainOnboardingRequestTrace,
        generation: ModelCallStats,
    ) -> None:
        trace.first_model_calls += generation.model_calls
        trace.first_usage.add(self._usage_from_stats(generation))
        trace.first_call_duration_ms += generation.duration_ms
        if generation.model_calls and not generation.usage_reported:
            trace.first_unreported_usage_calls += generation.model_calls

    def _record_retry_model_stats(
        self,
        trace: DomainOnboardingRequestTrace,
        stats: ModelCallStats,
    ) -> None:
        if trace.repair_reason not in {"llm_targeted_repair", "llm_repair_failed"}:
            return
        trace.retry_model_calls = stats.model_calls
        trace.retry_usage = self._usage_from_stats(stats)
        trace.retry_call_duration_ms = stats.duration_ms
        if stats.model_calls and not stats.usage_reported:
            trace.retry_unreported_usage_calls += stats.model_calls

    @staticmethod
    def _record_first(trace: DomainOnboardingRequestTrace, quality: ContentQuality) -> None:
        trace.first_score = quality.score
        trace.first_dimensions = dict(quality.dimensions)

    def _record_retrieval_stats(
        self,
        trace: DomainOnboardingRequestTrace,
        stats: RetrievalStats,
        *,
        accumulate: bool = False,
    ) -> None:
        values = {
            "retrieval_error_count": len(stats.errors),
            "retrieval_retry_count": stats.retry_count,
            "retrieval_cache_hit_count": stats.cache_hit_count,
            "retrieval_request_count": stats.request_count,
            "retrieval_source_success_count": stats.source_success_count,
            "retrieval_source_failure_count": stats.source_failure_count,
        }
        for field_name, value in values.items():
            if accumulate:
                setattr(trace, field_name, int(getattr(trace, field_name)) + value)
            else:
                setattr(trace, field_name, value)


def create_default_pipeline(
    model: Any,
    config: DomainOnboardingConfig | None = None,
) -> DomainOnboardingPipeline:
    settings = config or DomainOnboardingConfig()
    generator = StructuredOnboardingGenerator(model, settings)
    retry_policy = RetrievalRetryPolicy(
        max_attempts=settings.retrieval_max_attempts,
        base_backoff_seconds=settings.retrieval_backoff_seconds,
        max_backoff_seconds=settings.retrieval_max_backoff_seconds,
    )
    common_retrieval_options = {
        "retry_policy": retry_policy,
        "cache_ttl_seconds": settings.retrieval_cache_ttl_seconds,
        "cache_max_entries": settings.retrieval_cache_max_entries,
    }
    return DomainOnboardingPipeline(
        profile_builder=RuleBasedProfileBuilder(),
        planner=StormLitePlanner(model, settings),
        retriever=CompositePaperRetriever(
            [
                SemanticScholarRetriever(
                    timeout=settings.retrieval_timeout_seconds,
                    api_key=os.getenv("SEMANTIC_SCHOLAR_API_KEY") or None,
                    **common_retrieval_options,
                ),
                ArxivRetriever(
                    timeout=settings.retrieval_timeout_seconds,
                    min_interval_seconds=settings.arxiv_min_interval_seconds,
                    **common_retrieval_options,
                ),
                CrossrefRetriever(
                    timeout=settings.retrieval_timeout_seconds,
                    mailto=os.getenv("CROSSREF_MAILTO") or None,
                    **common_retrieval_options,
                ),
            ],
            max_workers=settings.retrieval_source_workers,
        ),
        ranker=WeightedPaperRanker(settings),
        generator=generator,
        evaluator=CompositeQualityEvaluator(settings),
        repairer=TargetedRepairer(generator, settings),
        config=settings,
    )
