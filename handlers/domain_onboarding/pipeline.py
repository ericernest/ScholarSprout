"""编排领域入门 V1 的画像、规划、检索、排序、生成、评估与修复。"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

from runtime.agent_runner import TokenUsage

from .adaptive_repair import AdaptiveRepairAdvisor, load_advisor
from .config import DomainOnboardingConfig
from .coverage import PaperCoverageAnalyzer
from .execution import PipelineExecutionContext, PipelineExecutionHalted
from .generator import GenerationError, StructuredOnboardingGenerator
from .graph_path_planner import GraphBasedPathPlanner
from .graph_validator import DomainKnowledgeGraphValidator
from .knowledge_graph import DomainKnowledgeGraphBuilder
from .metrics import DomainOnboardingRequestTrace
from .model_routing import routed_model_from_env, routing_snapshot
from .planner import StormLitePlanner
from .profile import RuleBasedProfileBuilder
from .quality import CompositeQualityEvaluator
from .ranking import WeightedPaperRanker
from .repair import TargetedRepairer
from .repair_selection import RepairSelectionPolicy
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
    QualityAttempt,
    RankingStats,
    RepairRecord,
    RetrievalStats,
)
from .text_similarity import (
    CachedEmbeddingTextVectorizer,
    FastEmbedProvider,
    OpenAIEmbeddingProvider,
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
        coverage_analyzer: Any | None = None,
        selection_policy: RepairSelectionPolicy | None = None,
        repair_advisor: AdaptiveRepairAdvisor | None = None,
        graph_builder: Any | None = None,
        graph_validator: Any | None = None,
        graph_path_planner: Any | None = None,
    ) -> None:
        self.profile_builder = profile_builder
        self.planner = planner
        self.retriever = retriever
        self.ranker = ranker
        self.generator = generator
        self.evaluator = evaluator
        self.repairer = repairer
        self.config = config
        self.policy = config.to_policy()
        self.coverage_analyzer = coverage_analyzer or PaperCoverageAnalyzer(config)
        self.selection_policy = selection_policy or RepairSelectionPolicy(
            self.policy.min_improvement_delta,
            self.policy.critical_dimensions,
        )
        self.repair_advisor = repair_advisor
        self.graph_builder = graph_builder or DomainKnowledgeGraphBuilder()
        self.graph_validator = graph_validator or DomainKnowledgeGraphValidator()
        self.graph_path_planner = graph_path_planner or GraphBasedPathPlanner()

    def run(
        self,
        request: DomainOnboardingRequest,
        trace: DomainOnboardingRequestTrace,
        execution_context: PipelineExecutionContext | None = None,
        progress_callback: Callable[[str, float, bool, list[str], dict[str, Any]], None] | None = None,
    ) -> PipelineResult:
        trace.policy_version = self.policy.policy_version
        trace.policy_fingerprint = self.policy.fingerprint
        context = execution_context or PipelineExecutionContext(
            timeout_seconds=self.config.request_timeout_seconds
        )
        partial_output = None
        partial_quality = None
        quality_attempts: list[QualityAttempt] = []
        partial_repair_record: RepairRecord | None = None
        try:
            profile, trace.profile_duration_ms = context.call(
                "profile",
                self.config.profile_timeout_seconds,
                self.profile_builder.build,
                request,
            )
            self._emit(progress_callback, "profile_ready", 0.12, True, ["learner_profile"], {"learner_profile": profile.model_dump(mode="json")})
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
                return self._result(status="planning_failed", query=request.query, error=str(error))
            plan = planning_result.plan
            self._record_planning_model_stats(trace, planning_result.stats)
            trace.search_query_count = len(plan.search_queries)
            self._emit(progress_callback, "plan_ready", 0.25, True, ["research_plan"], {"research_plan": plan.model_dump(mode="json")})

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
                return self._result(status="retrieval_failed", query=request.query, error=str(error))

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
            self._record_ranking_stats(trace, ranking_result.stats)
            trace.verified_paper_count = max(0, trace.deduplicated_paper_count - trace.invalid_paper_count)
            trace.selected_paper_count = len(ranked)
            coverage = self.coverage_analyzer.analyze(plan, ranked)
            trace.initial_coverage_gap_count = len(coverage.gaps)
            if coverage.gaps:
                ranked = self._supplement_papers(
                    plan,
                    candidates,
                    ranked,
                    coverage.gaps,
                    trace,
                    context,
                )
            final_coverage = self.coverage_analyzer.analyze(plan, ranked)
            trace.final_coverage_gap_count = len(final_coverage.gaps)
            if not ranked:
                return self._result(
                    status="retrieval_failed",
                    query=request.query,
                    error="No verified papers were returned by the configured data source.",
                )

            self._emit(
                progress_callback,
                "papers_ready",
                0.42,
                True,
                ["papers"],
                {"papers": [paper.model_dump(mode="json") for paper in ranked]},
            )

            try:
                incremental = getattr(self.generator, "generate_incrementally", None)
                if callable(incremental) and progress_callback is not None:
                    generation_result, trace.generation_duration_ms = context.call(
                        "generation",
                        self.config.generation_timeout_seconds,
                        incremental,
                        request,
                        profile,
                        plan,
                        ranked,
                        lambda event, data, paths: self._emit(
                            progress_callback,
                            event,
                            {"development_ready": 0.58, "landscape_ready": 0.70, "learning_path_ready": 0.80}[event],
                            True,
                            paths,
                            data,
                        ),
                    )
                else:
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
                return self._result(status="generation_failed", query=request.query, error=str(error))
            self._record_generation_model_stats(trace, generation_result.stats)
            partial_output = output
            output.reproducibility.update(
                {
                    "request_id": trace.request_id,
                    "policy_fingerprint": self.policy.fingerprint,
                    "ranking_vectorizer_backend": trace.ranking_vectorizer_backend,
                    "ranking_vectorizer_fallback_used": trace.ranking_vectorizer_fallback_used,
                    "canonical_registry_version": getattr(
                        getattr(self.ranker, "canonical_registry", None),
                        "version",
                        "unknown",
                    ),
                }
            )
            planning_route = routing_snapshot(
                getattr(self.planner, "model", None)
            )
            if planning_route is not None:
                output.reproducibility["planning_model_route"] = planning_route
            trace.evidence_claim_count = len(output.evidence_claims)

            first_quality, trace.evaluation_duration_ms = context.call(
                "evaluation",
                self.config.evaluation_timeout_seconds,
                self.evaluator.evaluate,
                output,
                ranked,
            )
            self._bind_quality_policy(first_quality)
            partial_quality = first_quality
            quality_attempts.append(
                QualityAttempt(
                    attempt_number=1,
                    source="initial",
                    quality=first_quality,
                    duration_ms=trace.evaluation_duration_ms,
                )
            )
            self._record_first(trace, first_quality)
            self._emit(progress_callback, "quality_ready", 0.88, True, ["quality"], {"quality": first_quality.model_dump(mode="json")})
            if first_quality.passed_hard_gates and first_quality.score >= first_quality.threshold:
                initial_record = RepairRecord(
                    triggered=False,
                    decision=self.selection_policy.initial(first_quality),
                    policy_version=self.policy.policy_version,
                    policy_fingerprint=self.policy.fingerprint,
                )
                self._record_final(trace, first_quality, initial_record)
                return self._result(
                    status=self._quality_status(first_quality),
                    query=request.query,
                    output=output,
                    quality=first_quality,
                    quality_attempts=quality_attempts,
                    repair_record=initial_record,
                    knowledge_graph=self._build_knowledge_graph(
                        output, first_quality, trace
                    ),
                )

            partial_repair_record = RepairRecord(
                triggered=True,
                policy_version=self.policy.policy_version,
                policy_fingerprint=self.policy.fingerprint,
            )
            self._emit(progress_callback, "repair_started", 0.91, True, [], {"issues": [item.model_dump(mode="json") for item in first_quality.issues]})
            self._attach_shadow_recommendations(partial_repair_record, first_quality, trace)
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
            partial_repair_record = repair_result.record
            partial_repair_record.policy_version = self.policy.policy_version
            partial_repair_record.policy_fingerprint = self.policy.fingerprint
            self._attach_shadow_recommendations(partial_repair_record, first_quality, trace)
            trace.repair_reason = repair_result.action
            self._record_retry_model_stats(trace, repair_result.stats)
            retry_quality, extra_eval_ms = context.call(
                "evaluation",
                self.config.evaluation_timeout_seconds,
                self.evaluator.evaluate,
                repaired,
                ranked,
            )
            self._bind_quality_policy(retry_quality)
            trace.evaluation_duration_ms += extra_eval_ms
            quality_attempts.append(
                QualityAttempt(
                    attempt_number=2,
                    source=(
                        "llm_repair"
                        if repair_result.action == "llm_targeted_repair"
                        else "code_repair"
                    ),
                    quality=retry_quality,
                    duration_ms=extra_eval_ms,
                )
            )

            decision = self.selection_policy.decide(
                first_quality,
                retry_quality,
                repair_result.record,
            )
            repair_result.record.decision = decision
            use_retry = decision.decision == "repaired_selected"
            if use_retry:
                retry_quality = self._with_retry(retry_quality, selected=2, status="improved")
                trace.retry_status = "improved"
                selected_output = repaired
                selected_quality = retry_quality
                repaired_payload = selected_output.model_dump(mode="json")
                changed_paths = sorted(
                    {
                        path.split(".", 1)[0].split("[", 1)[0]
                        for action in repair_result.record.actions
                        for path in action.changed_paths
                        if path
                    }
                )
                changed_paths = [
                    path for path in changed_paths if path in repaired_payload
                ] or ["development_stages", "current_landscape", "learning_path"]
                self._emit(
                    progress_callback,
                    "section_replaced",
                    0.96,
                    True,
                    changed_paths,
                    {path: repaired_payload[path] for path in changed_paths},
                )
            else:
                first_quality = self._with_retry(first_quality, selected=1, status="not_improved")
                trace.retry_status = "not_improved"
                selected_output = output
                selected_quality = first_quality
            self._record_final(trace, selected_quality, repair_result.record)
            trace.quality_delta = round(selected_quality.score - float(trace.first_score or 0.0), 6)
            return self._result(
                status=self._quality_status(selected_quality),
                query=request.query,
                output=selected_output,
                quality=selected_quality,
                quality_attempts=quality_attempts,
                repair_record=repair_result.record,
                knowledge_graph=self._build_knowledge_graph(
                    selected_output, selected_quality, trace
                ),
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
            return self._result(
                status=error.status,
                query=request.query,
                output=partial_output,
                quality=partial_quality,
                quality_attempts=quality_attempts,
                repair_record=partial_repair_record,
                error=str(error),
            )
        except Exception as error:
            return self._result(status="internal_error", query=request.query, error=str(error))

    def close(self) -> None:
        close = getattr(self.retriever, "close", None)
        if callable(close):
            close()

    @staticmethod
    def _emit(
        callback: Callable[[str, float, bool, list[str], dict[str, Any]], None] | None,
        event: str,
        progress: float,
        provisional: bool,
        replace_paths: list[str],
        data: dict[str, Any],
    ) -> None:
        if callback is not None:
            callback(event, progress, provisional, replace_paths, data)

    def _result(self, **values: Any) -> PipelineResult:
        return PipelineResult(
            policy_version=self.policy.policy_version,
            policy_fingerprint=self.policy.fingerprint,
            **values,
        )

    def _bind_quality_policy(self, quality: ContentQuality) -> None:
        quality.policy_version = self.policy.policy_version
        quality.policy_fingerprint = self.policy.fingerprint

    def _attach_shadow_recommendations(
        self,
        record: RepairRecord,
        quality: ContentQuality,
        trace: DomainOnboardingRequestTrace,
    ) -> None:
        if self.repair_advisor is None:
            return
        record.adaptive_policy_version = self.repair_advisor.policy.policy_version
        record.shadow_recommendations = self.repair_advisor.recommend(quality)
        trace.adaptive_policy_version = record.adaptive_policy_version
        trace.adaptive_recommendations = dict(record.shadow_recommendations)

    def _build_knowledge_graph(
        self,
        output: Any,
        quality: ContentQuality,
        trace: DomainOnboardingRequestTrace,
    ) -> Any | None:
        if not self.config.knowledge_graph_enabled or not quality.passed_hard_gates:
            return None
        started = time.perf_counter()
        try:
            graph = self.graph_builder.build(
                output,
                request_id=trace.request_id,
                quality_policy_version=self.policy.policy_version,
            )
            graph.validation = self.graph_validator.validate(graph)
            graph.path_plan = self.graph_path_planner.plan(graph)
            trace.knowledge_graph_node_count = len(graph.nodes)
            trace.knowledge_graph_edge_count = len(graph.edges)
            trace.knowledge_graph_valid = graph.validation.valid
            trace.knowledge_graph_fallback_used = bool(
                graph.path_plan and graph.path_plan.fallback_used
            )
            return graph
        except Exception:
            trace.knowledge_graph_build_failed = True
            return None
        finally:
            trace.knowledge_graph_duration_ms += (time.perf_counter() - started) * 1000

    def _supplement_papers(
        self,
        plan: Any,
        candidates: list[Any],
        ranked: list[Any],
        gaps: list[Any],
        trace: DomainOnboardingRequestTrace,
        context: PipelineExecutionContext,
    ) -> list[Any]:
        queries = list(
            dict.fromkeys(
                query
                for gap in gaps
                for query in gap.supplemental_queries
                if query.strip()
            )
        )[: self.config.search_queries_limit]
        if not queries:
            return ranked
        trace.supplemental_query_count += len(queries)
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
        self._record_ranking_stats(trace, ranking_result.stats)
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
        if quality.score < quality.threshold or quality.issues:
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
        trace.first_quality_state = quality.state
        trace.evidence_validation_modes = dict(quality.evidence_validation_modes)
        for issue in quality.issues:
            trace.first_issue_type_counts[issue.issue_type] = (
                trace.first_issue_type_counts.get(issue.issue_type, 0) + 1
            )
        for gate in quality.hard_gates:
            if gate.status == "failed":
                trace.hard_gate_failure_counts[gate.gate] = (
                    trace.hard_gate_failure_counts.get(gate.gate, 0) + 1
                )
        trace.unsupported_claim_count = sum(
            issue.issue_type == "unsupported_claim" for issue in quality.issues
        )
        trace.missing_evidence_count = sum(
            issue.issue_type == "missing_evidence" for issue in quality.issues
        )

    @staticmethod
    def _record_final(
        trace: DomainOnboardingRequestTrace,
        quality: ContentQuality,
        repair_record: RepairRecord,
    ) -> None:
        trace.final_score = quality.score
        trace.final_dimensions = dict(quality.dimensions)
        trace.final_quality_state = quality.state
        for action in repair_record.actions:
            key = f"{action.action_type}:{action.status}"
            trace.repair_action_status_counts[key] = (
                trace.repair_action_status_counts.get(key, 0) + 1
            )
        if repair_record.decision:
            trace.repair_selection_reasons = list(repair_record.decision.reasons)
            trace.repair_dimension_deltas = dict(
                repair_record.decision.dimension_deltas
            )
        trace.repair_changed_path_count = len(
            {
                path
                for action in repair_record.actions
                for path in action.changed_paths
            }
        )

    @staticmethod
    def _record_ranking_stats(
        trace: DomainOnboardingRequestTrace,
        stats: RankingStats,
    ) -> None:
        trace.ranking_vectorizer_backend = stats.vectorizer_backend
        trace.ranking_vectorizer_fallback_used = (
            trace.ranking_vectorizer_fallback_used or stats.vectorizer_fallback_used
        )
        trace.low_relevance_filtered_count = stats.low_relevance_filtered_count

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
            "retrieval_rate_limit_count": stats.rate_limit_count,
            "retrieval_stale_cache_hit_count": stats.stale_cache_hit_count,
            "retrieval_circuit_open_count": stats.circuit_open_count,
        }
        for field_name, value in values.items():
            if accumulate:
                setattr(trace, field_name, int(getattr(trace, field_name)) + value)
            else:
                setattr(trace, field_name, value)
        provider_values = {
            name: provider.model_dump(mode="json", exclude={"provider"})
            for name, provider in stats.providers.items()
        }
        if not accumulate:
            trace.retrieval_provider_stats = provider_values
            return
        for name, provider in provider_values.items():
            current = trace.retrieval_provider_stats.setdefault(name, {})
            for field_name, value in provider.items():
                if isinstance(value, bool):
                    current[field_name] = bool(current.get(field_name, False) or value)
                elif isinstance(value, (int, float)):
                    current[field_name] = current.get(field_name, 0) + value


def create_default_pipeline(
    model: Any,
    config: DomainOnboardingConfig | None = None,
) -> DomainOnboardingPipeline:
    settings = config or DomainOnboardingConfig()
    planning_model = routed_model_from_env(
        model,
        os.getenv("DOMAIN_ONBOARDING_PLANNING_MODELS"),
        route_name="planning",
    )
    configured_generation = os.getenv("DOMAIN_ONBOARDING_GENERATION_MODELS")
    if not configured_generation:
        configured_generation = getattr(getattr(model, "config", None), "model_name", "")
    generation_model = routed_model_from_env(
        model,
        configured_generation,
        route_name="generation",
    )
    section_models = {}
    for section in ("development", "landscape", "learning_path"):
        configured = os.getenv(
            f"DOMAIN_ONBOARDING_{section.upper()}_MODELS"
        ) or configured_generation
        section_models[section] = routed_model_from_env(
            model,
            configured,
            route_name=section,
        )
    configured_repair = (
        os.getenv("DOMAIN_ONBOARDING_REPAIR_MODELS")
        or configured_generation
    )
    repair_model = routed_model_from_env(
        model,
        configured_repair,
        route_name="repair",
    )
    generator = StructuredOnboardingGenerator(
        generation_model,
        settings,
        section_models=section_models,
        repair_model=repair_model,
    )
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
    local_embedding_model = os.getenv(
        "DOMAIN_ONBOARDING_LOCAL_EMBEDDING_MODEL", ""
    ).strip()
    remote_embedding_model = os.getenv(
        "DOMAIN_ONBOARDING_EMBEDDING_MODEL", ""
    ).strip()
    embedding_provider = None
    if local_embedding_model:
        embedding_provider = FastEmbedProvider(
            local_embedding_model,
            cache_dir=os.getenv("DOMAIN_ONBOARDING_EMBEDDING_CACHE_DIR") or None,
        )
    elif remote_embedding_model:
        embedding_provider = OpenAIEmbeddingProvider(model, remote_embedding_model)
    vectorizer = (
        CachedEmbeddingTextVectorizer(
            embedding_provider,
            batch_size=settings.embedding_batch_size,
            cache_max_entries=settings.embedding_cache_max_entries,
        )
        if embedding_provider is not None
        else None
    )
    return DomainOnboardingPipeline(
        profile_builder=RuleBasedProfileBuilder(),
        planner=StormLitePlanner(planning_model, settings),
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
            circuit_failure_threshold=settings.retrieval_circuit_failure_threshold,
            circuit_cooldown_seconds=settings.retrieval_circuit_cooldown_seconds,
            stale_cache_seconds=settings.retrieval_stale_cache_seconds,
            max_queries_per_source=settings.retrieval_queries_per_source,
        ),
        ranker=WeightedPaperRanker(settings, vectorizer=vectorizer),
        generator=generator,
        evaluator=CompositeQualityEvaluator(settings, evidence_vectorizer=vectorizer),
        repairer=TargetedRepairer(generator, settings),
        config=settings,
        repair_advisor=load_advisor(
            os.getenv("DOMAIN_ONBOARDING_ADAPTIVE_POLICY_FILE")
        ),
    )
