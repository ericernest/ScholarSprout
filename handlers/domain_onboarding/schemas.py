"""领域入门 V1 的模块间稳定数据契约。"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Preference = Literal["theory_first", "experiment_first", "balanced"]
PaperRole = Literal["survey", "foundational", "method", "evaluation", "frontier", "other"]
QualityIssueType = Literal[
    "structure_error",
    "invalid_paper",
    "missing_coverage",
    "weak_development_stage",
    "route_conflict",
    "beginner_mismatch",
    "format_error",
    "missing_evidence",
    "unsupported_claim",
]
RetryStatus = Literal[
    "not_needed",
    "improved",
    "not_improved",
    "invalid_response",
    "llm_failed",
    "retrieval_failed",
]

DOI_PATTERN = re.compile(r"^10\.\d{4,9}/[-._;()/:a-z0-9]+$", re.IGNORECASE)
ARXIV_ID_PATTERN = re.compile(
    r"^(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[a-z-]+)?/\d{7})$",
    re.IGNORECASE,
)


def stable_id(prefix: str, value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:36]
    digest = hashlib.sha1(value.strip().lower().encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{normalized or 'item'}_{digest}"


class OnboardingModel(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class DomainOnboardingRequest(OnboardingModel):
    query: str
    session_id: str | None = None
    user_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        query = value.strip()
        if not query:
            raise ValueError("query must not be empty")
        return query


class LearnerProfile(OnboardingModel):
    background: list[str] = Field(default_factory=list)
    goal: str = "建立领域基础认知并具备阅读代表论文的能力"
    time_budget_weeks: int | None = Field(default=None, ge=1, le=260)
    preference: Preference = "balanced"
    known_concepts: list[str] = Field(default_factory=list)


class ResearchPerspective(OnboardingModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    questions: list[str] = Field(default_factory=list)


class DomainResearchPlan(OnboardingModel):
    normalized_domain: str = Field(min_length=1)
    perspectives: list[ResearchPerspective] = Field(min_length=3)
    search_queries: list[str] = Field(min_length=1)
    expected_subdirections: list[str] = Field(min_length=3)

    @model_validator(mode="after")
    def deduplicate(self) -> "DomainResearchPlan":
        self.search_queries = list(dict.fromkeys(q.strip() for q in self.search_queries if q.strip()))
        self.expected_subdirections = list(
            dict.fromkeys(s.strip() for s in self.expected_subdirections if s.strip())
        )
        return self


class CoverageGap(OnboardingModel):
    subdirection_id: str
    subdirection: str
    missing_roles: list[PaperRole] = Field(default_factory=list)
    reason: str
    supplemental_queries: list[str] = Field(default_factory=list)


class CoverageAnalysis(OnboardingModel):
    gaps: list[CoverageGap] = Field(default_factory=list)
    covered_subdirections: dict[str, list[str]] = Field(default_factory=dict)
    covered_roles: list[PaperRole] = Field(default_factory=list)


class PaperCandidate(OnboardingModel):
    paper_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    abstract: str | None = None
    year: int | None = Field(default=None, ge=1800, le=2100)
    url: str
    citation_count: int | None = Field(default=None, ge=0)
    source: str
    matched_queries: list[str] = Field(default_factory=list)
    doi: str | None = None
    arxiv_id: str | None = None
    publication_types: list[str] = Field(default_factory=list)

    @field_validator("paper_id", "title", "url", "source")
    @classmethod
    def required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("paper identity fields must not be empty")
        return value

    @field_validator("doi", mode="before")
    @classmethod
    def normalize_doi(cls, value: object) -> str | None:
        if value is None or not str(value).strip():
            return None
        normalized = re.sub(
            r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)",
            "",
            str(value).strip(),
            flags=re.IGNORECASE,
        ).lower()
        if not DOI_PATTERN.fullmatch(normalized):
            raise ValueError("invalid DOI")
        return normalized

    @field_validator("arxiv_id", mode="before")
    @classmethod
    def normalize_arxiv_id(cls, value: object) -> str | None:
        if value is None or not str(value).strip():
            return None
        normalized = re.sub(
            r"^(?:https?://arxiv\.org/(?:abs|pdf)/|arxiv:\s*)",
            "",
            str(value).strip(),
            flags=re.IGNORECASE,
        )
        normalized = re.sub(r"\.pdf$", "", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"v\d+$", "", normalized, flags=re.IGNORECASE).lower()
        if not ARXIV_ID_PATTERN.fullmatch(normalized):
            raise ValueError("invalid arXiv identifier")
        return normalized

    @field_validator("publication_types", mode="before")
    @classmethod
    def normalize_publication_types(cls, value: object) -> list[str]:
        values = value if isinstance(value, list) else ([value] if value else [])
        return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))


class RankedPaper(PaperCandidate):
    relevance_score: float = Field(ge=0.0, le=1.0)
    citation_score: float = Field(ge=0.0, le=1.0)
    recency_score: float = Field(ge=0.0, le=1.0)
    diversity_score: float = Field(ge=0.0, le=1.0)
    final_score: float = Field(ge=0.0, le=1.0)
    paper_role: PaperRole = "other"


class SelectedPaper(OnboardingModel):
    paper_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    abstract: str | None = None
    year: int | None = None
    url: str
    citation_count: int | None = None
    source: str
    doi: str | None = None
    arxiv_id: str | None = None
    publication_types: list[str] = Field(default_factory=list)
    paper_role: PaperRole = "other"
    final_score: float = Field(default=0.0, ge=0.0, le=1.0)

    @classmethod
    def from_ranked(cls, paper: RankedPaper) -> "SelectedPaper":
        return cls.model_validate(paper.model_dump())


class PaperReference(OnboardingModel):
    paper_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    url: str
    contribution: str = ""


class Prerequisite(OnboardingModel):
    prerequisite_id: str | None = None
    name: str
    why_needed: str = ""
    key_points: list[str] = Field(default_factory=list)
    related_paper_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def ensure_id(self) -> "Prerequisite":
        self.prerequisite_id = self.prerequisite_id or stable_id("pre", self.name)
        return self


class DevelopmentStage(OnboardingModel):
    stage_id: str | None = None
    name: str
    summary: str = ""
    motivation: str = ""
    representative_papers: list[PaperReference] = Field(default_factory=list)
    core_concepts: list[str] = Field(default_factory=list)
    main_techniques: list[str] = Field(default_factory=list)
    open_problems: list[str] = Field(default_factory=list)
    related_paper_ids: list[str] = Field(default_factory=list)
    prerequisite_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def ensure_id(self) -> "DevelopmentStage":
        self.stage_id = self.stage_id or stable_id("stage", self.name)
        return self


class CurrentLandscape(OnboardingModel):
    problems: list[str] = Field(default_factory=list)
    subdirections: list[str] = Field(default_factory=list)
    subdirection_ids: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def ensure_ids(self) -> "CurrentLandscape":
        self.subdirection_ids = {
            name: self.subdirection_ids.get(name) or stable_id("sub", name)
            for name in self.subdirections
        }
        return self


class LearningStep(OnboardingModel):
    step: str
    goal: str = ""
    topics: list[str] = Field(default_factory=list)
    papers: list[PaperReference] = Field(default_factory=list)
    paper_ids: list[str] = Field(default_factory=list)
    activities: list[str] = Field(default_factory=list)
    completion_criteria: list[str] = Field(default_factory=list)
    expected_outcome: str = ""

    @field_validator("step", mode="before")
    @classmethod
    def stringify_step(cls, value: Any) -> str:
        return str(value).strip()


class EvidenceClaim(OnboardingModel):
    claim_id: str | None = None
    claim: str = Field(min_length=1)
    supporting_paper_ids: list[str] = Field(default_factory=list)
    support_type: Literal[
        "abstract_explicit",
        "metadata_inference",
        "background_synthesis",
    ] = "background_synthesis"

    @model_validator(mode="after")
    def ensure_id(self) -> "EvidenceClaim":
        self.claim_id = self.claim_id or stable_id("claim", self.claim)
        self.supporting_paper_ids = list(dict.fromkeys(self.supporting_paper_ids))
        return self


class DomainOnboardingOutput(OnboardingModel):
    domain: str
    text: str
    learner_profile: LearnerProfile
    prerequisites: list[Prerequisite]
    development_stages: list[DevelopmentStage]
    current_landscape: CurrentLandscape
    learning_path: list[LearningStep]
    papers: list[SelectedPaper]
    evidence_claims: list[EvidenceClaim] = Field(default_factory=list)


class QualityIssue(OnboardingModel):
    issue_type: QualityIssueType
    severity: Literal["warning", "error", "critical"]
    target_path: str
    message: str
    recommended_action: str


class ContentQuality(OnboardingModel):
    score: float = Field(ge=0.0, le=1.0)
    threshold: float = Field(ge=0.0, le=1.0)
    passed_hard_gates: bool
    dimensions: dict[str, float]
    issues: list[QualityIssue] = Field(default_factory=list)
    attempts: int = Field(default=1, ge=1, le=2)
    selected_attempt: int = Field(default=1, ge=1, le=2)
    retry_status: RetryStatus = "not_needed"


class PipelineResult(OnboardingModel):
    status: Literal[
        "ok",
        "quality_warning",
        "quality_failed",
        "invalid_input",
        "planning_failed",
        "retrieval_failed",
        "generation_failed",
        "timeout",
        "cancelled",
        "internal_error",
    ]
    mode: Literal["domain_onboarding"] = "domain_onboarding"
    query: str
    output: DomainOnboardingOutput | None = None
    quality: ContentQuality | None = None
    error: str | None = None

    def to_response(self) -> dict[str, Any]:
        if self.output is None:
            return self.model_dump(mode="json", exclude_none=True)
        payload = self.output.model_dump(mode="json")
        payload.update(
            status=self.status,
            mode=self.mode,
            query=self.query,
            quality=self.quality.model_dump(mode="json") if self.quality else None,
        )
        if self.error:
            payload["error"] = self.error
        return payload


class ModelCallStats(OnboardingModel):
    duration_ms: float = 0.0
    model_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    usage_reported: bool = False


class PlanningResult(OnboardingModel):
    plan: DomainResearchPlan
    stats: ModelCallStats = Field(default_factory=ModelCallStats)


class ProviderRetrievalStats(OnboardingModel):
    provider: str
    success: bool = False
    latency_ms: float = 0.0
    result_count: int = 0
    error_count: int = 0
    retry_count: int = 0
    cache_hit_count: int = 0
    request_count: int = 0
    rate_limit_count: int = 0
    circuit_open: bool = False
    circuit_skipped: bool = False
    stale_cache_used: bool = False


class RetrievalStats(OnboardingModel):
    errors: list[str] = Field(default_factory=list)
    retry_count: int = 0
    cache_hit_count: int = 0
    request_count: int = 0
    source_success_count: int = 0
    source_failure_count: int = 0
    rate_limit_count: int = 0
    stale_cache_hit_count: int = 0
    circuit_open_count: int = 0
    providers: dict[str, ProviderRetrievalStats] = Field(default_factory=dict)

    def add(self, other: "RetrievalStats") -> None:
        self.errors.extend(other.errors)
        self.retry_count += other.retry_count
        self.cache_hit_count += other.cache_hit_count
        self.request_count += other.request_count
        self.source_success_count += other.source_success_count
        self.source_failure_count += other.source_failure_count
        self.rate_limit_count += other.rate_limit_count
        self.stale_cache_hit_count += other.stale_cache_hit_count
        self.circuit_open_count += other.circuit_open_count
        self.providers.update(other.providers)


class RetrievalResult(OnboardingModel):
    papers: list[PaperCandidate] = Field(default_factory=list)
    stats: RetrievalStats = Field(default_factory=RetrievalStats)


class RankingStats(OnboardingModel):
    deduplicated_count: int = 0
    invalid_count: int = 0
    candidate_source_counts: dict[str, int] = Field(default_factory=dict)
    mmr_scores: dict[str, float] = Field(default_factory=dict)
    vectorizer_backend: str = "unknown"
    vectorizer_fallback_used: bool = False
    low_relevance_filtered_count: int = 0


class RankingResult(OnboardingModel):
    papers: list[RankedPaper] = Field(default_factory=list)
    stats: RankingStats = Field(default_factory=RankingStats)


class GenerationResult(OnboardingModel):
    output: DomainOnboardingOutput
    stats: ModelCallStats = Field(default_factory=ModelCallStats)


class RepairResult(OnboardingModel):
    output: DomainOnboardingOutput
    action: Literal[
        "code_repair",
        "llm_targeted_repair",
        "llm_repair_failed",
    ]
    stats: ModelCallStats = Field(default_factory=ModelCallStats)
