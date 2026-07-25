"""编排领域入门 V1 的画像、规划、检索、排序、生成、评估与修复。"""

from __future__ import annotations

import os
from time import perf_counter
from typing import Any

from runtime.agent_runner import TokenUsage

from .config import DomainOnboardingConfig
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
from .schemas import ContentQuality, DomainOnboardingRequest, ModelCallStats, PipelineResult


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
    ) -> PipelineResult:
        self._reset_model_stats()
        try:
            profile, trace.profile_duration_ms = self._timed(self.profile_builder.build, request)
            planning_started = perf_counter()
            try:
                plan = self.planner.plan(request.query, profile)
            except Exception as error:
                trace.planning_duration_ms = round((perf_counter() - planning_started) * 1000, 3)
                self._record_planning_model_stats(trace)
                return PipelineResult(status="planning_failed", query=request.query, error=str(error))
            trace.planning_duration_ms = round((perf_counter() - planning_started) * 1000, 3)
            self._record_planning_model_stats(trace)
            trace.search_query_count = len(plan.search_queries)

            try:
                candidates, trace.retrieval_duration_ms = self._timed(
                    self.retriever.search,
                    plan.search_queries,
                    limit_per_query=self.config.papers_per_query,
                )
            except PaperRetrievalError as error:
                self._record_retrieval_stats(trace)
                return PipelineResult(status="retrieval_failed", query=request.query, error=str(error))

            trace.retrieved_paper_count = len(candidates)
            self._record_retrieval_stats(trace)
            ranked, trace.ranking_duration_ms = self._timed(
                self.ranker.rank,
                candidates,
                plan,
                limit=self.config.selected_paper_limit,
            )
            trace.deduplicated_paper_count = getattr(self.ranker, "last_deduplicated_count", len(candidates))
            trace.invalid_paper_count = getattr(self.ranker, "last_invalid_count", 0)
            trace.verified_paper_count = max(0, trace.deduplicated_paper_count - trace.invalid_paper_count)
            trace.selected_paper_count = len(ranked)
            if not ranked:
                return PipelineResult(
                    status="retrieval_failed",
                    query=request.query,
                    error="No verified papers were returned by the configured data source.",
                )

            generation_started = perf_counter()
            try:
                output = self.generator.generate(request, profile, plan, ranked)
            except GenerationError as error:
                return PipelineResult(status="generation_failed", query=request.query, error=str(error))
            finally:
                trace.generation_duration_ms = round(
                    (perf_counter() - generation_started) * 1000,
                    3,
                )
                self._record_generation_model_stats(trace)

            first_quality, trace.evaluation_duration_ms = self._timed(
                self.evaluator.evaluate, output, ranked
            )
            self._record_first(trace, first_quality)
            if first_quality.passed_hard_gates and first_quality.score >= first_quality.threshold:
                trace.final_score = first_quality.score
                trace.final_dimensions = dict(first_quality.dimensions)
                return PipelineResult(status="ok", query=request.query, output=output, quality=first_quality)

            if (
                any(issue.issue_type == "missing_coverage" for issue in first_quality.issues)
                and len(ranked) < self.config.selected_paper_limit
            ):
                ranked = self._supplement_papers(plan, candidates, ranked, trace)

            repaired, trace.repair_duration_ms = self._timed(
                self.repairer.repair,
                request,
                profile,
                plan,
                output,
                first_quality,
                ranked,
            )
            trace.repair_reason = getattr(self.repairer, "last_action", "unknown")
            self._record_retry_model_stats(trace)
            retry_quality, extra_eval_ms = self._timed(self.evaluator.evaluate, repaired, ranked)
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
        except Exception as error:
            return PipelineResult(status="internal_error", query=request.query, error=str(error))

    def close(self) -> None:
        close = getattr(self.retriever, "close", None)
        if callable(close):
            close()

    def _supplement_papers(self, plan: Any, candidates: list[Any], ranked: list[Any], trace: DomainOnboardingRequestTrace) -> list[Any]:
        queries = [
            f'"{plan.normalized_domain}" "{subdirection}" survey method evaluation'
            for subdirection in plan.expected_subdirections
        ][: self.config.search_queries_limit]
        if not queries:
            return ranked
        try:
            extra, duration = self._timed(
                self.retriever.search,
                queries,
                limit_per_query=self.config.papers_per_query,
            )
        except PaperRetrievalError:
            self._record_retrieval_stats(trace, accumulate=True)
            return ranked
        trace.retrieval_duration_ms += duration
        self._record_retrieval_stats(trace, accumulate=True)
        trace.search_query_count += len(queries)
        trace.retrieved_paper_count += len(extra)
        reranked, duration = self._timed(
            self.ranker.rank,
            [*candidates, *extra],
            plan,
            limit=self.config.selected_paper_limit,
        )
        trace.ranking_duration_ms += duration
        trace.deduplicated_paper_count = getattr(self.ranker, "last_deduplicated_count", len(candidates) + len(extra))
        trace.invalid_paper_count = getattr(self.ranker, "last_invalid_count", 0)
        trace.verified_paper_count = max(0, trace.deduplicated_paper_count - trace.invalid_paper_count)
        trace.selected_paper_count = len(reranked)
        return reranked or ranked

    @staticmethod
    def _timed(function: Any, *args: Any, **kwargs: Any) -> tuple[Any, float]:
        started = perf_counter()
        result = function(*args, **kwargs)
        return result, round((perf_counter() - started) * 1000, 3)

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

    def _reset_model_stats(self) -> None:
        for component in (self.planner, self.generator):
            if hasattr(component, "last_stats"):
                try:
                    component.last_stats = ModelCallStats()
                except (AttributeError, TypeError):
                    continue

    def _record_planning_model_stats(self, trace: DomainOnboardingRequestTrace) -> None:
        planning = getattr(self.planner, "last_stats", ModelCallStats())
        trace.first_model_calls += planning.model_calls
        trace.first_usage = self._usage_from_stats(planning)
        trace.first_call_duration_ms += planning.duration_ms
        if planning.model_calls and not planning.usage_reported:
            trace.first_unreported_usage_calls += planning.model_calls

    def _record_generation_model_stats(self, trace: DomainOnboardingRequestTrace) -> None:
        generation = getattr(self.generator, "last_stats", ModelCallStats())
        trace.first_model_calls += generation.model_calls
        trace.first_usage.add(self._usage_from_stats(generation))
        trace.first_call_duration_ms += generation.duration_ms
        if generation.model_calls and not generation.usage_reported:
            trace.first_unreported_usage_calls += generation.model_calls

    def _record_retry_model_stats(self, trace: DomainOnboardingRequestTrace) -> None:
        if trace.repair_reason not in {"llm_targeted_repair", "llm_repair_failed"}:
            return
        stats = getattr(self.generator, "last_stats", ModelCallStats())
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
        *,
        accumulate: bool = False,
    ) -> None:
        values = {
            "retrieval_error_count": len(getattr(self.retriever, "last_errors", [])),
            "retrieval_retry_count": int(getattr(self.retriever, "last_retry_count", 0)),
            "retrieval_cache_hit_count": int(getattr(self.retriever, "last_cache_hits", 0)),
            "retrieval_request_count": int(getattr(self.retriever, "last_request_count", 0)),
            "retrieval_source_success_count": int(
                getattr(self.retriever, "last_source_success_count", 0)
            ),
            "retrieval_source_failure_count": int(
                getattr(self.retriever, "last_source_failure_count", 0)
            ),
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
