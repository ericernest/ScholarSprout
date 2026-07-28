"""集中管理领域入门 V1 的数量、权重和质量阈值。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .policy import (
    CURRENT_POLICY_VERSION,
    DomainOnboardingPolicy,
    default_critical_dimensions,
    default_dimension_weights,
    default_hard_gate_dimensions,
    default_llm_repair_issue_types,
)
from .schemas import QualityDimension, QualityIssueType


class DomainOnboardingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    evidence_support_threshold: float = Field(default=0.08, ge=0.0, le=1.0)
    policy_version: str = Field(
        default=CURRENT_POLICY_VERSION,
        pattern=r"^domain-quality-v\d+\.\d+\.\d+$",
    )
    quality_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    min_improvement_delta: float = Field(default=0.05, ge=0.0, le=1.0)
    quality_dimension_weights: dict[QualityDimension, float] = Field(
        default_factory=default_dimension_weights
    )
    hard_gate_dimensions: dict[str, list[QualityDimension]] = Field(
        default_factory=default_hard_gate_dimensions
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
    # Calibrated from the 2026-07-27 controlled online run (3 real domains):
    # total 111-147s, planning 13-26s, generation 78-103s.
    request_timeout_seconds: float = Field(default=300.0, gt=0.0, le=600.0)
    profile_timeout_seconds: float = Field(default=5.0, gt=0.0, le=60.0)
    planning_timeout_seconds: float = Field(default=45.0, gt=0.0, le=120.0)
    retrieval_stage_timeout_seconds: float = Field(default=45.0, gt=0.0, le=300.0)
    ranking_timeout_seconds: float = Field(default=10.0, gt=0.0, le=60.0)
    generation_timeout_seconds: float = Field(default=120.0, gt=0.0, le=180.0)
    evaluation_timeout_seconds: float = Field(default=10.0, gt=0.0, le=60.0)
    repair_timeout_seconds: float = Field(default=120.0, gt=0.0, le=120.0)
    relevance_weight: float = Field(default=0.55, ge=0.0, le=1.0)
    citation_weight: float = Field(default=0.20, ge=0.0, le=1.0)
    recency_weight: float = Field(default=0.15, ge=0.0, le=1.0)
    diversity_weight: float = Field(default=0.10, ge=0.0, le=1.0)
    mmr_lambda: float = Field(default=0.70, ge=0.0, le=1.0)
    mmr_role_bonus: float = Field(default=0.05, ge=0.0, le=0.25)
    retrieval_timeout_seconds: float = Field(default=12.0, gt=0.0, le=60.0)
    retrieval_max_attempts: int = Field(default=3, ge=1, le=5)
    retrieval_backoff_seconds: float = Field(default=0.5, ge=0.0, le=10.0)
    retrieval_max_backoff_seconds: float = Field(default=8.0, ge=0.0, le=60.0)
    retrieval_cache_ttl_seconds: float = Field(default=3600.0, ge=0.0, le=86400.0)
    retrieval_cache_max_entries: int = Field(default=256, ge=0, le=4096)
    retrieval_source_workers: int = Field(default=3, ge=1, le=8)
    retrieval_circuit_failure_threshold: int = Field(default=3, ge=1, le=20)
    retrieval_circuit_cooldown_seconds: float = Field(default=30.0, ge=0.0, le=600.0)
    retrieval_stale_cache_seconds: float = Field(default=86400.0, ge=0.0, le=604800.0)
    arxiv_min_interval_seconds: float = Field(default=3.0, ge=0.0, le=10.0)
    knowledge_graph_enabled: bool = False
    knowledge_graph_shadow_mode: bool = True

    @model_validator(mode="after")
    def validate_settings(self) -> "DomainOnboardingConfig":
        if self.selected_paper_limit > self.candidate_paper_limit:
            raise ValueError("selected_paper_limit must not exceed candidate_paper_limit")
        total = (
            self.relevance_weight
            + self.citation_weight
            + self.recency_weight
            + self.diversity_weight
        )
        if abs(total - 1.0) > 1e-9:
            raise ValueError("ranking weights must sum to 1.0")
        if self.retrieval_max_backoff_seconds < self.retrieval_backoff_seconds:
            raise ValueError("retrieval_max_backoff_seconds must not be smaller than base backoff")
        if self.knowledge_graph_enabled and not self.knowledge_graph_shadow_mode:
            raise ValueError("knowledge graph foundation supports shadow mode only")
        self.to_policy()
        return self

    def to_policy(self) -> DomainOnboardingPolicy:
        return DomainOnboardingPolicy(
            policy_version=self.policy_version,
            quality_threshold=self.quality_threshold,
            min_improvement_delta=self.min_improvement_delta,
            dimension_weights=self.quality_dimension_weights,
            hard_gate_dimensions=self.hard_gate_dimensions,
            hard_gate_severities=self.hard_gate_severities,
            llm_repair_issue_types=self.llm_repair_issue_types,
            critical_dimensions=self.critical_quality_dimensions,
        )
