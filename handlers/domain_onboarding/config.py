"""集中管理领域入门 V1 的数量、权重和质量阈值。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .policy import (
    CURRENT_POLICY_VERSION,
    DomainOnboardingPolicy,
    default_critical_dimensions,
    default_dimension_weights,
    default_hard_gate_dimensions,
    default_hard_gate_min_scores,
    default_llm_repair_issue_types,
)
from .schemas import QualityDimension, QualityIssueType


class DomainOnboardingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def discard_legacy_paper_score_weights(cls, value: Any) -> Any:
        """Accept old local configs without reviving the retired score formula."""

        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        for key in (
            "relevance_weight",
            "recency_weight",
            "diversity_weight",
            "ranking_path_fusion_weight",
            "ranking_missing_abstract_penalty",
            "ranking_rrf_k",
        ):
            migrated.pop(key, None)
        return migrated

    search_queries_limit: int = Field(default=6, ge=1, le=12)
    papers_per_query: int = Field(default=10, ge=1, le=50)
    candidate_paper_limit: int = Field(default=40, ge=1, le=200)
    selected_paper_limit: int = Field(default=12, ge=1, le=40)
    require_verified_paper_year: bool = True
    min_development_stages: int = Field(default=3, ge=1, le=8)
    min_subdirections: int = Field(default=3, ge=1, le=12)
    min_learning_steps: int = Field(default=4, ge=1, le=8)
    coverage_similarity_threshold: float = Field(default=0.08, ge=0.0, le=1.0)
    ranking_min_relevance_score: float = Field(default=0.05, ge=0.0, le=1.0)
    ranking_canonical_relevance_floor: float = Field(default=0.10, ge=0.0, le=1.0)
    quality_min_paper_relevance_score: float = Field(default=0.05, ge=0.0, le=1.0)
    quality_paper_relevance_threshold: float = Field(default=0.70, ge=0.0, le=1.0)
    enforce_core_paper_coverage: bool = True
    min_core_papers: int = Field(default=1, ge=0, le=5)
    evidence_support_threshold: float = Field(default=0.08, ge=0.0, le=1.0)
    policy_version: str = Field(
        default=CURRENT_POLICY_VERSION,
        pattern=r"^domain-quality-v\d+\.\d+\.\d+$",
    )
    quality_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    # A validated output remains useful even when evidence/coverage gates need
    # attention. Production therefore delivers it with warnings after repair;
    # strict mode remains available for offline calibration and audits.
    quality_gate_enforcement: Literal["warn", "strict"] = "warn"
    min_improvement_delta: float = Field(default=0.05, ge=0.0, le=1.0)
    quality_dimension_weights: dict[QualityDimension, float] = Field(
        default_factory=default_dimension_weights
    )
    hard_gate_dimensions: dict[str, list[QualityDimension]] = Field(
        default_factory=default_hard_gate_dimensions
    )
    hard_gate_min_scores: dict[str, float] = Field(
        default_factory=default_hard_gate_min_scores
    )
    hard_gate_severities: list[Literal["error", "critical"]] = Field(
        default_factory=lambda: ["error", "critical"]
    )
    llm_repair_issue_types: list[QualityIssueType] = Field(
        default_factory=default_llm_repair_issue_types
    )
    critical_quality_dimensions: list[QualityDimension] = Field(
        default_factory=default_critical_dimensions
    )
    max_content_repairs: int = Field(default=1, ge=0, le=1)
    # Incremental clients receive validated sections before terminal completion.
    # The larger envelope also covers remote embedding and staged generation.
    request_timeout_seconds: float = Field(default=4800.0, gt=0.0, le=7200.0)
    profile_timeout_seconds: float = Field(default=5.0, gt=0.0, le=60.0)
    # The stage deadline must exceed the model-call timeout so a timed-out LLM
    # call can return to StormLitePlanner and activate its deterministic fallback.
    planning_timeout_seconds: float = Field(default=300.0, gt=0.0, le=600.0)
    planning_model_timeout_seconds: float = Field(
        default=280.0, gt=0.0, le=590.0
    )
    # A composite retrieval call may process four queries per source and retry
    # transient 429/5xx responses. The old 45 s budget expired after the call
    # returned, discarding otherwise usable degraded results on real networks.
    retrieval_stage_timeout_seconds: float = Field(default=180.0, gt=0.0, le=300.0)
    # Remote qwen3 embeddings can legitimately take much longer than a lexical
    # rank, especially for the larger per-stage evidence pools. Keep the stage
    # bounded by the request deadline without discarding a successful batch.
    ranking_timeout_seconds: float = Field(default=180.0, gt=0.0, le=300.0)
    generation_timeout_seconds: float = Field(default=3600.0, gt=0.0, le=4800.0)
    evaluation_timeout_seconds: float = Field(default=300.0, gt=0.0, le=600.0)
    repair_timeout_seconds: float = Field(default=300.0, gt=0.0, le=600.0)
    # One public paper score is used everywhere. Context, identity, paper type,
    # and minimum relevance remain non-compensating gates outside this formula.
    paper_score_relevance_weight: float = Field(default=0.65, ge=0.0, le=1.0)
    paper_score_query_coverage_weight: float = Field(default=0.15, ge=0.0, le=1.0)
    paper_score_recency_weight: float = Field(default=0.10, ge=0.0, le=1.0)
    paper_score_metadata_weight: float = Field(default=0.10, ge=0.0, le=1.0)
    mmr_lambda: float = Field(default=0.70, ge=0.0, le=1.0)
    mmr_role_bonus: float = Field(default=0.05, ge=0.0, le=0.25)
    ranking_min_path_relevance_score: float = Field(default=0.03, ge=0.0, le=1.0)
    ranking_path_pool_multiplier: int = Field(default=2, ge=1, le=5)
    ranking_role_pool_multiplier: int = Field(default=2, ge=1, le=5)
    ranking_required_roles: list[Literal["survey", "foundational", "method", "evaluation", "application", "frontier"]] = Field(
        default_factory=lambda: ["survey", "foundational", "method", "evaluation", "frontier"]
    )
    ranking_min_role_coverage: int = Field(default=3, ge=0, le=5)
    ranking_max_application_papers: int = Field(default=1, ge=0, le=10)
    ranking_max_survey_papers: int = Field(default=3, ge=0, le=10)
    ranking_max_evaluation_papers: int = Field(default=3, ge=0, le=10)
    ranking_max_method_papers: int = Field(default=4, ge=0, le=10)
    ranking_min_abstract_candidates: int = Field(default=6, ge=0, le=40)
    subdirection_retrieval_enabled: bool = True
    subdirection_queries_per_direction: int = Field(default=2, ge=1, le=4)
    subdirection_papers_per_direction: int = Field(default=4, ge=1, le=8)
    subdirection_min_papers: int = Field(default=3, ge=1, le=8)
    subdirection_min_abstract_papers: int = Field(default=2, ge=0, le=8)
    subdirection_max_supplemental_queries: int = Field(default=1, ge=0, le=2)
    citation_enrichment_enabled: bool = True
    citation_enrichment_batch_size: int = Field(default=50, ge=1, le=500)
    paper_recommendations_enabled: bool = True
    recommendation_survey_query_limit: int = Field(default=4, ge=1, le=8)
    recommendation_query_candidate_limit: int = Field(default=10, ge=2, le=24)
    recommendation_probe_limit_per_query: int = Field(default=5, ge=1, le=20)
    recommendation_discovered_term_limit: int = Field(default=6, ge=1, le=12)
    recommendation_min_query_score: float = Field(default=0.35, ge=0.0, le=1.0)
    recommendation_survey_limit: int = Field(default=3, ge=1, le=6)
    recommendation_reference_limit: int = Field(default=3, ge=0, le=8)
    recommendation_references_per_survey: int = Field(default=40, ge=1, le=100)
    recommendation_recent_year_window: int = Field(default=5, ge=2, le=10)
    embedding_batch_size: int = Field(default=32, ge=1, le=128)
    embedding_cache_max_entries: int = Field(default=2048, ge=0, le=32768)
    # Embeddings improve relevance but must never become a single point of
    # failure. A timed-out remote batch falls back to multilingual TF-IDF.
    embedding_timeout_seconds: float = Field(default=30.0, gt=0.0, le=120.0)
    development_stage_planning_timeout_seconds: float = Field(
        default=65.0, gt=0.0, le=120.0
    )
    max_development_stage_plans: int = Field(default=4, ge=3, le=6)
    staged_development_enabled: bool = True
    generation_max_attempts: int = Field(default=3, ge=1, le=3)
    generation_retry_backoff_seconds: float = Field(default=30.0, ge=0.0, le=60.0)
    generation_development_workers: int = Field(default=1, ge=1, le=3)
    # Landscape and the standard learning path are independent once the
    # development narrative is ready, so run both remote calls concurrently.
    generation_section_workers: int = Field(default=2, ge=1, le=2)
    stage_queries_per_stage: int = Field(default=2, ge=1, le=4)
    stage_papers_per_stage: int = Field(default=3, ge=1, le=6)
    # Incremental generation runs development first, then landscape/path in
    # parallel. The default deadline fits three full attempts in both waves.
    generation_section_timeout_seconds: float = Field(default=60.0, gt=0.0, le=120.0)
    generation_development_timeout_seconds: float = Field(
        default=360.0, gt=0.0, le=600.0
    )
    generation_development_foundation_timeout_seconds: float = Field(
        default=240.0, gt=0.0, le=600.0
    )
    generation_development_stage_timeout_seconds: float = Field(
        default=300.0, gt=0.0, le=600.0
    )
    generation_landscape_timeout_seconds: float = Field(
        default=300.0, gt=0.0, le=600.0
    )
    generation_learning_path_timeout_seconds: float = Field(
        default=300.0, gt=0.0, le=600.0
    )
    generation_paper_abstract_max_chars: int = Field(default=700, ge=200, le=4000)
    retrieval_timeout_seconds: float = Field(default=8.0, gt=0.0, le=60.0)
    retrieval_max_attempts: int = Field(default=3, ge=1, le=5)
    retrieval_queries_per_source: int = Field(default=4, ge=1, le=12)
    retrieval_backoff_seconds: float = Field(default=2.0, ge=0.0, le=10.0)
    retrieval_max_backoff_seconds: float = Field(default=8.0, ge=0.0, le=60.0)
    retrieval_cache_ttl_seconds: float = Field(default=3600.0, ge=0.0, le=86400.0)
    retrieval_cache_max_entries: int = Field(default=256, ge=0, le=4096)
    retrieval_source_workers: int = Field(default=3, ge=1, le=8)
    retrieval_circuit_failure_threshold: int = Field(default=3, ge=1, le=20)
    retrieval_circuit_cooldown_seconds: float = Field(default=30.0, ge=0.0, le=600.0)
    retrieval_stale_cache_seconds: float = Field(default=86400.0, ge=0.0, le=604800.0)
    arxiv_min_interval_seconds: float = Field(default=3.0, ge=0.0, le=10.0)
    semantic_scholar_min_interval_seconds: float = Field(
        default=1.05, ge=0.0, le=10.0
    )
    knowledge_graph_enabled: bool = False
    knowledge_graph_shadow_mode: bool = True

    @model_validator(mode="after")
    def validate_settings(self) -> "DomainOnboardingConfig":
        if self.selected_paper_limit > self.candidate_paper_limit:
            raise ValueError("selected_paper_limit must not exceed candidate_paper_limit")
        if self.subdirection_min_papers > self.subdirection_papers_per_direction:
            raise ValueError(
                "subdirection_min_papers must not exceed subdirection_papers_per_direction"
            )
        if self.subdirection_min_abstract_papers > self.subdirection_papers_per_direction:
            raise ValueError(
                "subdirection_min_abstract_papers must not exceed subdirection_papers_per_direction"
            )
        total = (
            self.paper_score_relevance_weight
            + self.paper_score_query_coverage_weight
            + self.paper_score_recency_weight
            + self.paper_score_metadata_weight
        )
        if abs(total - 1.0) > 1e-9:
            raise ValueError("paper score weights must sum to 1.0")
        if self.retrieval_max_backoff_seconds < self.retrieval_backoff_seconds:
            raise ValueError("retrieval_max_backoff_seconds must not be smaller than base backoff")
        if self.planning_model_timeout_seconds >= self.planning_timeout_seconds:
            raise ValueError(
                "planning model timeout must be shorter than the planning stage deadline"
            )
        development_budget = self.generation_max_attempts * (
            self.generation_development_foundation_timeout_seconds
            + self.generation_development_stage_timeout_seconds
            if self.staged_development_enabled
            else self.generation_development_timeout_seconds
        )
        if self.staged_development_enabled:
            # After researched-stage exhaustion the generator makes a final
            # standard development-section attempt sequence.
            development_budget += (
                self.generation_max_attempts
                * self.generation_development_timeout_seconds
            )
        incremental_generation_budget = (
            development_budget
            + self.generation_max_attempts * max(
                self.generation_landscape_timeout_seconds,
                self.generation_learning_path_timeout_seconds,
            )
        )
        if incremental_generation_budget > self.generation_timeout_seconds:
            raise ValueError(
                "incremental development and parallel section budgets must fit inside the generation deadline"
            )
        if self.knowledge_graph_enabled and not self.knowledge_graph_shadow_mode:
            raise ValueError("knowledge graph foundation supports shadow mode only")
        if len(self.ranking_required_roles) != len(set(self.ranking_required_roles)):
            raise ValueError("ranking_required_roles must not contain duplicates")
        if self.ranking_min_role_coverage > len(self.ranking_required_roles):
            raise ValueError("ranking_min_role_coverage exceeds configured required roles")
        self.to_policy()
        return self

    def to_policy(self) -> DomainOnboardingPolicy:
        return DomainOnboardingPolicy(
            policy_version=self.policy_version,
            quality_threshold=self.quality_threshold,
            min_improvement_delta=self.min_improvement_delta,
            dimension_weights=self.quality_dimension_weights,
            hard_gate_dimensions=self.hard_gate_dimensions,
            hard_gate_min_scores=self.hard_gate_min_scores,
            hard_gate_severities=self.hard_gate_severities,
            llm_repair_issue_types=self.llm_repair_issue_types,
            critical_dimensions=self.critical_quality_dimensions,
        )
