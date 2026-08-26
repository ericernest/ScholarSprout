"""编排领域入门 V1 的画像、规划、检索、排序、生成、评估与修复。"""

from __future__ import annotations

import os
import time
import inspect
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
from .paper_recommendations import SurveyRecommendationPolicy
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
    FinalQualitySummary,
    ModelCallStats,
    PaperCandidate,
    PaperSearchQuery,
    PipelineResult,
    QualityAttempt,
    RankingStats,
    ResearchPerspective,
    RepairDecision,
    RepairRecord,
    RetrievalStats,
    SubdirectionEvidenceBundle,
)
from .subdirection_research import (
    SubdirectionPaperRanker,
    SubdirectionResearchPolicy,
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
        self.subdirection_policy = SubdirectionResearchPolicy(config)
        self.subdirection_ranker = SubdirectionPaperRanker(ranker, config)
        self.recommendation_policy = SurveyRecommendationPolicy(ranker, config)
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

        def publish_llm_delta(stage: str, delta: str) -> None:
            if not delta or progress_callback is None:
                return
            progress = {
                "planning": 0.18,
                "stage_planning": 0.43,
                "development_foundation": 0.59,
                "development_stage": 0.63,
                "development": 0.50,
                "landscape": 0.62,
                "learning_path": 0.72,
                "generation": 0.50,
                "repair": 0.93,
            }.get(stage, 0.50)
            self._emit(
                progress_callback,
                "llm_delta",
                progress,
                True,
                [],
                {"stage": stage, "delta": delta},
            )

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
                    self._call_with_optional_delta,
                    self.planner.plan,
                    request.query,
                    profile,
                    on_delta=publish_llm_delta,
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

            candidates = self._annotate_candidate_query_hints(
                retrieval_result.papers,
                plan.paper_queries,
            )
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

            subdirection_bundles: list[SubdirectionEvidenceBundle] = []
            all_candidates = list(candidates)
            if self.config.subdirection_retrieval_enabled:
                ranked, all_candidates, subdirection_bundles = (
                    self._research_subdirections(
                        plan,
                        all_candidates,
                        ranked,
                        trace,
                        context,
                        progress_callback,
                    )
                )

            stage_planner = getattr(self.generator, "plan_development_research", None)
            if self.config.staged_development_enabled and callable(stage_planner):
                try:
                    stage_result, trace.stage_planning_duration_ms = context.call(
                        "stage_planning",
                        self.config.development_stage_planning_timeout_seconds + 5.0,
                        self._call_with_optional_delta,
                        stage_planner,
                        request,
                        plan,
                        ranked,
                        on_delta=publish_llm_delta,
                    )
                    stage_plans, stage_stats = stage_result
                    plan.development_stage_plans = list(stage_plans)
                    trace.development_stage_count = len(plan.development_stage_plans)
                    self._record_generation_model_stats(trace, stage_stats)
                    self._emit(
                        progress_callback,
                        "stage_plan_ready",
                        0.45,
                        True,
                        ["research_plan"],
                        {"research_plan": plan.model_dump(mode="json")},
                    )
                    ranked = self._research_development_stages(
                        plan,
                        all_candidates,
                        ranked,
                        trace,
                        context,
                        progress_callback,
                    )
                except GenerationError as error:
                    trace.stage_planning_duration_ms = context.stage_durations_ms.get(
                        "stage_planning", 0.0
                    )
                    self._record_generation_model_stats(trace, error.stats)
                    # Stage planning improves the development timeline, but it is
                    # optional. A malformed JSON response must not fail the whole
                    # onboarding task; the generator can produce the standard
                    # development section when no stage plans are present.
                    plan.development_stage_plans = []
                    trace.development_stage_count = 0

            if subdirection_bundles:
                # _research_subdirections already merged these bundles before
                # stage research. Merging them a second time here can fill the
                # selection limit with subdirection papers and evict every
                # paper bound to an independently researched development stage.
                kept_ids = {paper.paper_id for paper in ranked}
                bundle_statuses = {
                    bundle.subdirection_id: bundle.status
                    for bundle in subdirection_bundles
                }
                for branch in plan.subdirection_plans:
                    branch.selected_paper_ids = [
                        paper_id
                        for paper_id in branch.selected_paper_ids
                        if paper_id in kept_ids
                    ]
                    branch.evidence_status = (
                        "sufficient"
                        if bundle_statuses.get(branch.subdirection_id) == "sufficient"
                        and len(branch.selected_paper_ids)
                        >= self.config.subdirection_min_papers
                        else "limited"
                    )
                for stage in plan.development_stage_plans:
                    stage.selected_paper_ids = [
                        paper_id
                        for paper_id in stage.selected_paper_ids
                        if paper_id in kept_ids
                    ]

            ranked = self._enrich_citations(ranked, trace, context)
            ranked = [
                paper.model_copy(update={"paper_usage": "evidence"})
                for paper in ranked
            ]
            if self.config.paper_recommendations_enabled:
                try:
                    ranked = self._build_paper_recommendations(
                        request,
                        plan,
                        all_candidates,
                        ranked,
                        trace,
                        context,
                    )
                except PipelineExecutionHalted:
                    raise
                except Exception as error:
                    trace.recommendation_degraded_count += 1
                    trace.recommendation_strategy = "survey_degraded_no_result"
                    plan.recommendation_strategy = "survey_degraded_no_result"
                    plan.recommendation_query_audit.append(
                        {
                            "query": "",
                            "source": "recommendation_pipeline",
                            "result_count": 0,
                            "survey_count": 0,
                            "score": 0.0,
                            "selected": False,
                            "reason": f"internal_degradation:{type(error).__name__}",
                        }
                    )
                    trace.recommendation_query_audit = list(
                        plan.recommendation_query_audit
                    )
            else:
                plan.recommendation_strategy = "disabled"
                trace.recommendation_strategy = "disabled"

            visible_papers = [
                paper
                for paper in ranked
                if paper.paper_usage in {"recommendation", "both"}
            ]
            if not self.config.paper_recommendations_enabled:
                visible_papers = ranked
            trace.selected_paper_count = len(visible_papers)

            self._emit(
                progress_callback,
                "papers_ready",
                0.56,
                True,
                ["papers"],
                {"papers": [paper.model_dump(mode="json") for paper in visible_papers]},
            )

            try:
                incremental = getattr(self.generator, "generate_incrementally", None)
                if callable(incremental) and progress_callback is not None:
                    generation_result, trace.generation_duration_ms = context.call(
                        "generation",
                        self.config.generation_timeout_seconds,
                        self._call_with_optional_delta,
                        incremental,
                        request,
                        profile,
                        plan,
                        ranked,
                        lambda event, data, paths: self._emit(
                            progress_callback,
                            event,
                            {"development_ready": 0.68, "landscape_ready": 0.78, "learning_path_ready": 0.85}[event],
                            True,
                            paths,
                            data,
                        ),
                        on_delta=publish_llm_delta,
                    )
                else:
                    generation_result, trace.generation_duration_ms = context.call(
                        "generation",
                        self.config.generation_timeout_seconds,
                        self._call_with_optional_delta,
                        self.generator.generate,
                        request,
                        profile,
                        plan,
                        ranked,
                        on_delta=publish_llm_delta,
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
                    "ranking_strategy": trace.ranking_strategy,
                    "ranking_path_candidate_counts": trace.ranking_path_candidate_counts,
                    "ranking_selected_path_counts": trace.ranking_selected_path_counts,
                    "ranking_role_candidate_counts": trace.ranking_role_candidate_counts,
                    "ranking_selected_role_counts": trace.ranking_selected_role_counts,
                    "canonical_registry_version": getattr(
                        getattr(self.ranker, "canonical_registry", None),
                        "version",
                        "unknown",
                    ),
                    "planning_mode": plan.planning_mode,
                    "planning_fallback_reason": plan.planning_fallback_reason,
                    "recommendation_strategy": trace.recommendation_strategy,
                    "recommendation_expanded_terms": list(
                        plan.recommendation_expanded_terms
                    ),
                    "recommendation_query_audit": list(
                        trace.recommendation_query_audit
                    ),
                    "generation_degraded_sections": list(
                        generation_result.stats.degraded_sections
                    ),
                    "generation_failure_reasons": list(
                        generation_result.stats.failure_reasons
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
            self._emit(progress_callback, "quality_ready", 0.90, True, ["quality"], {"quality": first_quality.model_dump(mode="json")})
            if first_quality.passed_hard_gates and first_quality.score >= first_quality.threshold:
                initial_record = RepairRecord(
                    triggered=False,
                    decision=self.selection_policy.initial(first_quality),
                    policy_version=self.policy.policy_version,
                    policy_fingerprint=self.policy.fingerprint,
                )
                self._record_final(trace, first_quality, initial_record)
                final_quality = self._final_quality_summary(
                    first_quality, quality_attempts, initial_record
                )
                self._emit(
                    progress_callback,
                    "final_quality_ready",
                    0.98,
                    False,
                    ["final_quality"],
                    {"final_quality": final_quality.model_dump(mode="json")},
                )
                return self._result(
                    status=self._quality_status(first_quality),
                    query=request.query,
                    output=output,
                    quality=first_quality,
                    quality_attempts=quality_attempts,
                    repair_record=initial_record,
                    final_quality=final_quality,
                    knowledge_graph=self._build_knowledge_graph(
                        output, first_quality, trace
                    ),
                )

            failed_gate_names = {
                gate.gate for gate in first_quality.hard_gates if gate.status == "failed"
            }
            hard_issues = [issue for issue in first_quality.issues if issue.hard_gate]
            degraded_evidence_only = (
                first_quality.score >= first_quality.threshold
                and failed_gate_names == {"evidence_support"}
                and bool(hard_issues)
                and all(issue.issue_type == "unsupported_claim" for issue in hard_issues)
                and first_quality.evidence_validation_modes.get(
                    "embedding_fallback", 0
                )
                > 0
            )
            if degraded_evidence_only:
                # When the semantic backend was unavailable, a low lexical
                # evidence score is not a sound reason to spend another full
                # LLM call rewriting otherwise threshold-passing content.
                record = RepairRecord(
                    triggered=False,
                    policy_version=self.policy.policy_version,
                    policy_fingerprint=self.policy.fingerprint,
                    decision=RepairDecision(
                        selected_attempt=1,
                        decision="initial_retained",
                        reasons=["evidence_validation_degraded"],
                    ),
                )
                self._record_final(trace, first_quality, record)
                final_quality = self._final_quality_summary(
                    first_quality, quality_attempts, record
                )
                self._emit(
                    progress_callback,
                    "final_quality_ready",
                    0.98,
                    False,
                    ["final_quality"],
                    {"final_quality": final_quality.model_dump(mode="json")},
                )
                return self._result(
                    status="quality_warning",
                    query=request.query,
                    output=output,
                    quality=first_quality,
                    quality_attempts=quality_attempts,
                    repair_record=record,
                    final_quality=final_quality,
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
                self._call_with_optional_delta,
                self.repairer.repair,
                request,
                profile,
                plan,
                output,
                first_quality,
                ranked,
                on_delta=publish_llm_delta,
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
            final_quality = self._final_quality_summary(
                selected_quality, quality_attempts, repair_result.record
            )
            self._emit(
                progress_callback,
                "final_quality_ready",
                0.98,
                False,
                ["final_quality"],
                {"final_quality": final_quality.model_dump(mode="json")},
            )
            return self._result(
                status=self._quality_status(selected_quality),
                query=request.query,
                output=selected_output,
                quality=selected_quality,
                quality_attempts=quality_attempts,
                repair_record=repair_result.record,
                final_quality=final_quality,
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
            if (
                error.status == "timeout"
                and error.stage in {"repair", "evaluation"}
                and partial_output is not None
                and partial_quality is not None
            ):
                # The user already has a validated onboarding artifact. A slow
                # optional repair must not turn that content into a failed job.
                fallback_quality = partial_quality.model_copy(
                    update={"retry_status": "llm_failed"}
                )
                trace.retry_status = "llm_failed"
                record = partial_repair_record or RepairRecord(triggered=True)
                record.decision = record.decision or RepairDecision(
                    selected_attempt=1,
                    decision="initial_retained",
                    reasons=["repair_execution_failed"],
                )
                self._record_final(trace, fallback_quality, record)
                final_quality = self._final_quality_summary(
                    fallback_quality, quality_attempts, record
                )
                self._emit(
                    progress_callback,
                    "final_quality_ready",
                    0.98,
                    False,
                    ["final_quality"],
                    {"final_quality": final_quality.model_dump(mode="json")},
                )
                return self._result(
                    status="quality_warning",
                    query=request.query,
                    output=partial_output,
                    quality=fallback_quality,
                    quality_attempts=quality_attempts,
                    repair_record=record,
                    final_quality=final_quality,
                    error=str(error),
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
    def _call_with_optional_delta(
        function: Callable[..., Any],
        *args: Any,
        on_delta: Callable[[str, str], None] | None = None,
    ) -> Any:
        """Preserve compatibility with test doubles and external implementations."""

        try:
            parameters = inspect.signature(function).parameters.values()
            supports_delta = any(
                item.name == "on_delta" or item.kind == inspect.Parameter.VAR_KEYWORD
                for item in parameters
            )
        except (TypeError, ValueError):
            supports_delta = False
        if supports_delta:
            return function(*args, on_delta=on_delta)
        return function(*args)

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
        if values.get("quality") is not None and values.get("final_quality") is None:
            values["final_quality"] = self._final_quality_summary(
                values["quality"],
                values.get("quality_attempts") or [],
                values.get("repair_record"),
            )
        return PipelineResult(
            policy_version=self.policy.policy_version,
            policy_fingerprint=self.policy.fingerprint,
            **values,
        )

    @staticmethod
    def _final_quality_summary(
        quality: ContentQuality,
        attempts: list[QualityAttempt],
        repair_record: RepairRecord | None,
    ) -> FinalQualitySummary:
        initial = attempts[0].quality if attempts else quality
        initial_dimensions = initial.dimensions
        final_dimensions = quality.dimensions
        issue_counts: dict[str, int] = {}
        for issue in quality.issues:
            issue_counts[issue.severity] = issue_counts.get(issue.severity, 0) + 1
        decision = repair_record.decision if repair_record is not None else None
        selection_reason = (
            decision.reasons[0]
            if decision is not None and decision.reasons
            else "quality_threshold_met"
        )
        return FinalQualitySummary(
            verdict=quality.state,
            initial_score=initial.score,
            final_score=quality.score,
            score_delta=round(quality.score - initial.score, 6),
            threshold=quality.threshold,
            selected_attempt=quality.selected_attempt,
            repair_applied=bool(
                repair_record
                and repair_record.triggered
                and quality.selected_attempt == 2
            ),
            selection_reason=selection_reason,
            passed_hard_gates=quality.passed_hard_gates,
            hard_gate_pass_count=sum(
                gate.status == "passed" for gate in quality.hard_gates
            ),
            hard_gate_total=len(quality.hard_gates),
            unresolved_issue_count=len(quality.issues),
            issue_counts_by_severity=issue_counts,
            dimension_deltas={
                name: round(score - initial_dimensions.get(name, score), 6)
                for name, score in final_dimensions.items()
            },
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
        supplemental_specs = [
            PaperSearchQuery(
                query=query,
                role_hint=role,
                path_id="",
                priority=1,
            )
            for gap in gaps
            for role in gap.missing_roles
            for query in gap.supplemental_queries
            if query.strip()
        ]
        extra = self._annotate_candidate_query_hints(
            retrieval_result.papers,
            supplemental_specs,
        )
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
    def _annotate_candidate_query_hints(
        papers: list[PaperCandidate],
        query_specs: list[PaperSearchQuery],
    ) -> list[PaperCandidate]:
        specs_by_query = {spec.query: spec for spec in query_specs}
        annotated: list[PaperCandidate] = []
        for paper in papers:
            matched_specs = [
                specs_by_query[query]
                for query in paper.matched_queries
                if query in specs_by_query
            ]
            if not matched_specs:
                annotated.append(paper)
                continue
            annotated.append(
                paper.model_copy(
                    update={
                        "matched_role_hints": list(
                            dict.fromkeys(
                                [
                                    *paper.matched_role_hints,
                                    *(spec.role_hint for spec in matched_specs),
                                ]
                            )
                        ),
                        "matched_path_hints": list(
                            dict.fromkeys(
                                [
                                    *paper.matched_path_hints,
                                    *(
                                        spec.path_id
                                        for spec in matched_specs
                                        if spec.path_id
                                    ),
                                ]
                            )
                        ),
                    }
                )
            )
        return annotated

    def _research_development_stages(
        self,
        plan: Any,
        candidates: list[Any],
        ranked: list[Any],
        trace: DomainOnboardingRequestTrace,
        context: PipelineExecutionContext,
        progress_callback: Callable[[str, float, bool, list[str], dict[str, Any]], None]
        | None,
    ) -> list[Any]:
        """Retrieve and rank real papers independently for each historical stage."""

        all_candidates = list(candidates)
        stage_rankings: list[list[Any]] = []
        stage_count = len(plan.development_stage_plans)
        trace.development_stage_count = stage_count
        for index, stage in enumerate(plan.development_stage_plans):
            queries = stage.search_queries[: self.config.stage_queries_per_stage]
            trace.search_query_count += len(queries)
            trace.stage_retrieval_query_count += len(queries)
            try:
                retrieval_result, duration = context.call(
                    "retrieval",
                    self.config.retrieval_stage_timeout_seconds,
                    self.retriever.search,
                    queries,
                    limit_per_query=self.config.papers_per_query,
                )
                trace.retrieval_duration_ms += duration
                self._record_retrieval_stats(
                    trace, retrieval_result.stats, accumulate=True
                )
                trace.retrieved_paper_count += len(retrieval_result.papers)
                all_candidates.extend(retrieval_result.papers)
            except PaperRetrievalError as error:
                self._record_retrieval_stats(trace, error.stats, accumulate=True)

            stage_plan = plan.model_copy(
                deep=True,
                update={
                    "search_queries": queries,
                    "perspectives": [
                        ResearchPerspective(
                            path_id=f"stage-{stage.sequence}",
                            name=stage.name,
                            description=stage.focus,
                            questions=[stage.focus],
                            search_queries=queries,
                        )
                    ],
                    "expected_subdirections": [
                        stage.name,
                        *plan.expected_subdirections,
                    ][: max(3, len(plan.expected_subdirections))],
                },
            )
            ranking_result, duration = context.call(
                "ranking",
                self.config.ranking_timeout_seconds,
                self.ranker.rank,
                all_candidates,
                stage_plan,
                limit=self.config.stage_papers_per_stage,
            )
            trace.ranking_duration_ms += duration
            self._record_ranking_stats(trace, ranking_result.stats)
            stage_papers = ranking_result.papers
            stage.selected_paper_ids = [paper.paper_id for paper in stage_papers]
            stage_rankings.append(stage_papers)
            progress = 0.46 + 0.08 * ((index + 1) / max(1, stage_count))
            self._emit(
                progress_callback,
                "stage_retrieval_ready",
                progress,
                True,
                ["research_plan"],
                {
                    "stage_id": stage.stage_id,
                    "stage_sequence": stage.sequence,
                    "research_plan": plan.model_dump(mode="json"),
                },
            )

        merged = self._merge_stage_rankings(ranked, stage_rankings)
        kept_ids = {paper.paper_id for paper in merged}
        for stage in plan.development_stage_plans:
            stage.selected_paper_ids = [
                paper_id
                for paper_id in stage.selected_paper_ids
                if paper_id in kept_ids
            ]
        trace.stage_bound_paper_count = len(
            {
                paper_id
                for stage in plan.development_stage_plans
                for paper_id in stage.selected_paper_ids
            }
        )
        trace.selected_paper_count = len(merged)
        return merged or ranked

    def _research_subdirections(
        self,
        plan: Any,
        candidates: list[Any],
        ranked: list[Any],
        trace: DomainOnboardingRequestTrace,
        context: PipelineExecutionContext,
        progress_callback: Callable[
            [str, float, bool, list[str], dict[str, Any]], None
        ]
        | None,
    ) -> tuple[list[Any], list[Any], list[SubdirectionEvidenceBundle]]:
        """Search and rank each planned branch without making one branch fatal."""

        all_candidates = list(candidates)
        bundles: list[SubdirectionEvidenceBundle] = []
        branches = list(plan.subdirection_plans[:3])
        for index, branch in enumerate(branches):
            queries = list(
                branch.search_queries[
                    : self.config.subdirection_queries_per_direction
                ]
            )
            direction_candidates: list[PaperCandidate] = []
            if queries:
                trace.search_query_count += len(queries)
                trace.subdirection_retrieval_query_count += len(queries)
                try:
                    retrieval_result, duration = context.call(
                        "retrieval",
                        self.config.retrieval_stage_timeout_seconds,
                        self.retriever.search,
                        [query.query for query in queries],
                        limit_per_query=self.config.papers_per_query,
                    )
                    trace.retrieval_duration_ms += duration
                    self._record_retrieval_stats(
                        trace,
                        retrieval_result.stats,
                        accumulate=True,
                    )
                    direction_candidates = self._annotate_candidate_query_hints(
                        retrieval_result.papers,
                        queries,
                    )
                    direction_candidates = (
                        self.subdirection_policy.exclude_out_of_scope(
                            direction_candidates,
                            branch,
                        )
                    )
                    trace.retrieved_paper_count += len(direction_candidates)
                    all_candidates.extend(direction_candidates)
                except PaperRetrievalError as error:
                    self._record_retrieval_stats(
                        trace,
                        error.stats,
                        accumulate=True,
                    )

            if direction_candidates:
                direction_candidates = self._enrich_citations(
                    direction_candidates,
                    trace,
                    context,
                )

            ranking_candidates = direction_candidates or list(candidates)
            direction_plan = self.subdirection_policy.direction_plan(
                plan,
                branch,
                queries
                or [
                    self.subdirection_policy.supplemental_query(
                        plan,
                        branch,
                        SubdirectionEvidenceBundle(
                            subdirection_id=branch.subdirection_id
                        ),
                    )
                ],
            )
            ranking_result, duration = context.call(
                "ranking",
                self.config.ranking_timeout_seconds,
                self.subdirection_ranker.rank,
                ranking_candidates,
                direction_plan,
                branch,
                limit=self.config.subdirection_papers_per_direction,
            )
            trace.ranking_duration_ms += duration
            self._record_ranking_stats(trace, ranking_result.stats)
            bundle = self.subdirection_policy.assess(
                branch,
                ranking_result.papers,
                query_count=len(queries),
                supplemental_query_count=0,
            )

            if (
                bundle.status == "limited"
                and self.config.subdirection_max_supplemental_queries > 0
            ):
                supplemental = self.subdirection_policy.supplemental_query(
                    plan,
                    branch,
                    bundle,
                )
                trace.search_query_count += 1
                trace.subdirection_retrieval_query_count += 1
                trace.subdirection_supplemental_query_count += 1
                try:
                    extra_result, duration = context.call(
                        "retrieval",
                        self.config.retrieval_stage_timeout_seconds,
                        self.retriever.search,
                        [supplemental.query],
                        limit_per_query=self.config.papers_per_query,
                    )
                    trace.retrieval_duration_ms += duration
                    self._record_retrieval_stats(
                        trace,
                        extra_result.stats,
                        accumulate=True,
                    )
                    extra = self._annotate_candidate_query_hints(
                        extra_result.papers,
                        [supplemental],
                    )
                    extra = self.subdirection_policy.exclude_out_of_scope(
                        extra,
                        branch,
                    )
                    if extra:
                        extra = self._enrich_citations(extra, trace, context)
                    trace.retrieved_paper_count += len(extra)
                    all_candidates.extend(extra)
                    direction_candidates.extend(extra)
                    rerank_plan = self.subdirection_policy.direction_plan(
                        plan,
                        branch,
                        [*queries, supplemental],
                    )
                    reranked, rank_duration = context.call(
                        "ranking",
                        self.config.ranking_timeout_seconds,
                        self.subdirection_ranker.rank,
                        direction_candidates or ranking_candidates,
                        rerank_plan,
                        branch,
                        limit=self.config.subdirection_papers_per_direction,
                    )
                    trace.ranking_duration_ms += rank_duration
                    self._record_ranking_stats(trace, reranked.stats)
                    bundle = self.subdirection_policy.assess(
                        branch,
                        reranked.papers,
                        query_count=len(queries),
                        supplemental_query_count=1,
                    )
                except PaperRetrievalError as error:
                    self._record_retrieval_stats(
                        trace,
                        error.stats,
                        accumulate=True,
                    )

            branch.selected_paper_ids = [
                paper.paper_id for paper in bundle.papers
            ]
            branch.evidence_status = bundle.status
            bundles.append(bundle)
            trace.subdirection_limited_count += int(bundle.status == "limited")
            progress = 0.31 + 0.10 * ((index + 1) / max(1, len(branches)))
            self._emit(
                progress_callback,
                "subdirection_retrieval_ready",
                progress,
                True,
                ["research_plan"],
                {
                    "subdirection_id": branch.subdirection_id,
                    "evidence_status": bundle.status,
                    "paper_count": len(bundle.papers),
                    "research_plan": plan.model_dump(mode="json"),
                },
            )

        merged = self.subdirection_policy.merge(ranked, bundles)
        trace.subdirection_bound_paper_count = len(
            {
                paper.paper_id
                for bundle in bundles
                for paper in bundle.papers
            }
        )
        trace.selected_paper_count = len(merged)
        return merged or ranked, all_candidates, bundles

    def _enrich_citations(
        self,
        papers: list[Any],
        trace: DomainOnboardingRequestTrace,
        context: PipelineExecutionContext,
    ) -> list[Any]:
        if not self.config.citation_enrichment_enabled or not papers:
            return papers
        enrich = getattr(self.retriever, "enrich_citations", None)
        if not callable(enrich):
            return papers
        try:
            result, duration = context.call(
                "retrieval",
                self.config.retrieval_stage_timeout_seconds,
                enrich,
                papers,
                batch_size=self.config.citation_enrichment_batch_size,
            )
        except Exception:
            trace.citation_enrichment_failure_count += 1
            return papers
        trace.retrieval_duration_ms += duration
        self._record_retrieval_stats(trace, result.stats, accumulate=True)
        trace.citation_enrichment_known_count = sum(
            paper.citation_status == "known" for paper in result.papers
        )
        trace.citation_enrichment_unknown_count = sum(
            paper.citation_status == "unknown" for paper in result.papers
        )
        trace.citation_enrichment_failure_count += int(bool(result.stats.errors))
        return result.papers or papers

    def _build_paper_recommendations(
        self,
        request: DomainOnboardingRequest,
        plan: Any,
        candidates: list[Any],
        evidence: list[Any],
        trace: DomainOnboardingRequestTrace,
        context: PipelineExecutionContext,
    ) -> list[Any]:
        """Build a survey-led display list while preserving all generation evidence."""

        query_audit: list[dict[str, Any]] = []
        survey_candidates: list[Any] = []
        discovery_candidates = [*evidence, *candidates]

        if self.recommendation_policy.english_domain_anchor(plan) is None:
            query_audit.append(
                {
                    "query": "",
                    "source": "domain_anchor_validation",
                    "result_count": 0,
                    "survey_count": 0,
                    "score": 0.0,
                    "selected": False,
                    "reason": "missing_standard_english_domain",
                }
            )
            trace.recommendation_degraded_count += 1

        def search_and_validate(
            queries: list[Any],
        ) -> tuple[list[Any], list[Any]]:
            if not queries:
                return [], []
            trace.search_query_count += len(queries)
            trace.recommendation_survey_query_count += len(queries)
            try:
                result, duration = context.call(
                    "retrieval",
                    self.config.retrieval_stage_timeout_seconds,
                    self.retriever.search,
                    [query.query for query in queries],
                    limit_per_query=self.config.recommendation_probe_limit_per_query,
                )
                trace.retrieval_duration_ms += duration
                self._record_retrieval_stats(trace, result.stats, accumulate=True)
                found = self._annotate_candidate_query_hints(result.papers, queries)
                trace.retrieved_paper_count += len(found)
                validation_result, ranking_duration = context.call(
                    "ranking",
                    self.config.ranking_timeout_seconds,
                    self.recommendation_policy.validate_queries,
                    queries,
                    found,
                    plan,
                )
                trace.ranking_duration_ms += ranking_duration
                selected_queries, audits = validation_result
                query_audit.extend(audits)
                trace.recommendation_validated_query_count += len(selected_queries)
                return (
                    self.recommendation_policy.candidates_for_queries(
                        found,
                        selected_queries,
                    ),
                    found,
                )
            except PaperRetrievalError as error:
                self._record_retrieval_stats(trace, error.stats, accumulate=True)
                trace.recommendation_degraded_count += 1
                query_audit.extend(
                    {
                        "query": query.query,
                        "source": "retrieval_error",
                        "result_count": 0,
                        "survey_count": 0,
                        "score": 0.0,
                        "selected": False,
                        "reason": "retrieval_error",
                    }
                    for query in queries
                )
                return [], []

        initial_queries = self.recommendation_policy.queries(plan)
        survey_candidates, initial_found = search_and_validate(initial_queries)

        if not survey_candidates:
            expansion_queries, expanded_terms = (
                self.recommendation_policy.discovered_queries(
                    plan,
                    [*discovery_candidates, *initial_found],
                )
            )
            if expansion_queries:
                trace.recommendation_expansion_round_count += 1
                expanded_candidates, _ = search_and_validate(expansion_queries)
                survey_candidates.extend(expanded_candidates)
                plan.recommendation_expanded_terms = expanded_terms

        if not survey_candidates:
            survey_candidates = self.recommendation_policy.evidence_survey_candidates(
                discovery_candidates
            )
            query_audit.append(
                {
                    "query": "",
                    "source": "evidence_survey_bootstrap",
                    "result_count": len(discovery_candidates),
                    "survey_count": len(survey_candidates),
                    "score": 1.0 if survey_candidates else 0.0,
                    "score_basis": "binary_metadata_verification",
                    "selected": bool(survey_candidates),
                    "reason": (
                        "verified_evidence_surveys"
                        if survey_candidates
                        else "no_verified_survey"
                    ),
                }
            )

        trace.recommendation_query_audit = query_audit
        plan.recommendation_query_audit = list(query_audit)

        survey_candidates = self._enrich_citations(
            survey_candidates,
            trace,
            context,
        )
        surveys, survey_candidate_count = self.recommendation_policy.select_surveys(
            survey_candidates,
            plan,
            language=request.language,
        )
        trace.recommendation_survey_candidate_count = survey_candidate_count
        trace.recommendation_selected_survey_count = len(surveys)

        references: list[Any] = []
        fetch_references = getattr(self.retriever, "fetch_references", None)
        if surveys and callable(fetch_references):
            try:
                reference_result, duration = context.call(
                    "retrieval",
                    self.config.retrieval_stage_timeout_seconds,
                    fetch_references,
                    surveys,
                    limit_per_paper=self.config.recommendation_references_per_survey,
                )
                trace.retrieval_duration_ms += duration
                self._record_retrieval_stats(
                    trace,
                    reference_result.stats,
                    accumulate=True,
                )
                trace.retrieved_paper_count += len(reference_result.papers)
                reference_candidates = self._enrich_citations(
                    reference_result.papers,
                    trace,
                    context,
                )
                references, reference_candidate_count = (
                    self.recommendation_policy.select_references(
                        reference_candidates,
                        plan,
                        language=request.language,
                    )
                )
                trace.recommendation_reference_candidate_count = (
                    reference_candidate_count
                )
                trace.recommendation_selected_reference_count = len(references)
                if reference_result.stats.errors:
                    trace.recommendation_degraded_count += 1
            except Exception:
                trace.recommendation_degraded_count += 1

        recommendations = [*surveys, *references]
        if not recommendations:
            trace.recommendation_degraded_count += 1
            plan.recommendation_strategy = "survey_degraded_no_result"
            trace.recommendation_strategy = "survey_degraded_no_result"
        else:
            plan.recommendation_strategy = "survey_success"
            trace.recommendation_strategy = "survey_success"
        return self.recommendation_policy.merge_with_evidence(
            evidence,
            recommendations,
        )

    def _merge_stage_rankings(
        self,
        global_ranked: list[Any],
        stage_rankings: list[list[Any]],
    ) -> list[Any]:
        limit = self.config.selected_paper_limit
        selected: list[Any] = []
        seen: set[str] = set()

        def add(paper: Any) -> None:
            if len(selected) >= limit or paper.paper_id in seen:
                return
            selected.append(paper)
            seen.add(paper.paper_id)

        for paper in global_ranked:
            if paper.is_canonical or paper.reading_priority == "core":
                add(paper)
        max_stage_size = max((len(items) for items in stage_rankings), default=0)
        for paper_index in range(max_stage_size):
            for papers in stage_rankings:
                if paper_index < len(papers):
                    add(papers[paper_index])
        for paper in global_ranked:
            add(paper)
        return selected

    @staticmethod
    def _with_retry(quality: ContentQuality, *, selected: int, status: str) -> ContentQuality:
        return quality.model_copy(
            update={"attempts": 2, "selected_attempt": selected, "retry_status": status}
        )

    def _quality_status(self, quality: ContentQuality) -> str:
        if (
            not quality.passed_hard_gates
            and self.config.quality_gate_enforcement == "strict"
        ):
            return "quality_failed"
        if (
            not quality.passed_hard_gates
            or quality.score < quality.threshold
            or quality.issues
        ):
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
        trace.ranking_strategy = stats.ranking_strategy
        trace.ranking_path_candidate_counts = dict(stats.per_path_candidate_counts)
        trace.ranking_selected_path_counts = dict(stats.selected_path_counts)
        trace.ranking_role_candidate_counts = dict(stats.per_role_candidate_counts)
        trace.ranking_selected_role_counts = dict(stats.selected_role_counts)
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
    *,
    embedding_model: Any | None = None,
    embedding_model_name: str | None = None,
) -> DomainOnboardingPipeline:
    settings = config or DomainOnboardingConfig()
    planning_model = routed_model_from_env(
        model,
        os.getenv("DOMAIN_ONBOARDING_PLANNING_MODELS"),
        route_name="planning",
    )
    configured_generation = os.getenv("DOMAIN_ONBOARDING_GENERATION_MODELS")
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
    configured_stage_planning = os.getenv(
        "DOMAIN_ONBOARDING_STAGE_PLANNING_MODELS"
    ) or os.getenv("DOMAIN_ONBOARDING_PLANNING_MODELS")
    section_models["stage_planning"] = routed_model_from_env(
        model,
        configured_stage_planning,
        route_name="stage_planning",
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
    embedding_enabled = os.getenv(
        "DOMAIN_ONBOARDING_EMBEDDING_ENABLED", "true"
    ).strip().lower() not in {"0", "false", "no", "off"}
    remote_embedding_model = os.getenv("DOMAIN_ONBOARDING_EMBEDDING_MODEL")
    if remote_embedding_model is None:
        remote_embedding_model = embedding_model_name or "qwen3-embedding"
    remote_embedding_model = remote_embedding_model.strip()
    embedding_provider = None
    if embedding_enabled and local_embedding_model:
        embedding_provider = FastEmbedProvider(
            local_embedding_model,
            cache_dir=os.getenv("DOMAIN_ONBOARDING_EMBEDDING_CACHE_DIR") or None,
        )
    elif embedding_enabled and remote_embedding_model:
        embedding_provider = OpenAIEmbeddingProvider(
            embedding_model or model,
            remote_embedding_model,
            timeout_seconds=settings.embedding_timeout_seconds,
        )
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
                    min_interval_seconds=(
                        settings.semantic_scholar_min_interval_seconds
                    ),
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
        coverage_analyzer=PaperCoverageAnalyzer(settings, vectorizer=vectorizer),
        generator=generator,
        evaluator=CompositeQualityEvaluator(settings, evidence_vectorizer=vectorizer),
        repairer=TargetedRepairer(generator, settings),
        config=settings,
        repair_advisor=load_advisor(
            os.getenv("DOMAIN_ONBOARDING_ADAPTIVE_POLICY_FILE")
        ),
    )
