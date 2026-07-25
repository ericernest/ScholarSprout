"""集中管理领域入门 V1 的数量、权重和质量阈值。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DomainOnboardingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_queries_limit: int = Field(default=6, ge=1, le=12)
    papers_per_query: int = Field(default=10, ge=1, le=50)
    candidate_paper_limit: int = Field(default=40, ge=1, le=200)
    selected_paper_limit: int = Field(default=12, ge=1, le=40)
    min_development_stages: int = Field(default=3, ge=1, le=8)
    min_subdirections: int = Field(default=3, ge=1, le=12)
    min_learning_steps: int = Field(default=4, ge=1, le=8)
    quality_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    min_improvement_delta: float = Field(default=0.05, ge=0.0, le=1.0)
    max_content_repairs: int = Field(default=1, ge=0, le=1)
    relevance_weight: float = Field(default=0.55, ge=0.0, le=1.0)
    citation_weight: float = Field(default=0.20, ge=0.0, le=1.0)
    recency_weight: float = Field(default=0.15, ge=0.0, le=1.0)
    diversity_weight: float = Field(default=0.10, ge=0.0, le=1.0)
    retrieval_timeout_seconds: float = Field(default=12.0, gt=0.0, le=60.0)
    retrieval_max_attempts: int = Field(default=3, ge=1, le=5)
    retrieval_backoff_seconds: float = Field(default=0.5, ge=0.0, le=10.0)
    retrieval_max_backoff_seconds: float = Field(default=8.0, ge=0.0, le=60.0)
    retrieval_cache_ttl_seconds: float = Field(default=3600.0, ge=0.0, le=86400.0)
    retrieval_cache_max_entries: int = Field(default=256, ge=0, le=4096)
    retrieval_source_workers: int = Field(default=3, ge=1, le=8)
    arxiv_min_interval_seconds: float = Field(default=3.0, ge=0.0, le=10.0)

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
        return self
