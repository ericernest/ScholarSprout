"""领域入门 V1 的模块间稳定数据契约。"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Preference = Literal["theory_first", "experiment_first", "balanced"]
PaperRole = Literal[
    "survey", "foundational", "method", "evaluation", "application", "frontier", "other"
]
ReadingPriority = Literal["core", "recommended", "optional", "extended"]
LearningUse = Literal[
    "concept_introduction",
    "architecture_reference",
    "method_extension",
    "baseline_implementation",
    "benchmark_dataset",
    "evaluation_framework",
    "frontier_problem",
]
ReadingMode = Literal["skim", "read", "reproduce", "evaluate"]
BindingStatus = Literal["policy_matched", "fallback"]
RelationStatus = Literal[
    "explicit", "paper_inferred", "semantic_inferred", "unresolved"
]
QualityDimension = Literal[
    "structure",
    "paper_validity",
    "paper_relevance",
    "evidence_grounding",
    "topic_coverage",
    "development_coherence",
    "learning_path",
    # Accepted only when replaying v1.6-and-earlier quality records.
    "goal_alignment",
    "language_alignment",
]
QualityState = Literal["passed", "warning", "failed"]
QualityGateStatus = Literal["passed", "failed", "not_evaluated"]
Repairability = Literal["code", "llm", "retrieval", "manual", "none"]
QualityAttemptSource = Literal["initial", "code_repair", "llm_repair"]
RepairActionType = Literal["code", "llm", "retrieval"]
RepairActionStatus = Literal["planned", "applied", "skipped", "failed"]
RepairSelection = Literal["initial_selected", "repaired_selected", "initial_retained"]
RepairDecisionReason = Literal[
    "quality_threshold_met",
    "significant_improvement",
    "hard_gate_failed",
    "improvement_too_small",
    "critical_dimension_regressed",
    "repair_execution_failed",
    "repair_output_invalid",
]
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
    "low_paper_relevance",
    "paper_context_mismatch",
    "missing_core_paper",
    "language_mismatch",
    "generation_fallback",
]
RetryStatus = Literal[
    "not_needed",
    "improved",
    "not_improved",
    "invalid_response",
    "llm_failed",
    "retrieval_failed",
]
KnowledgeNodeType = Literal[
    "domain", "prerequisite", "development_stage", "subdirection", "paper", "claim"
]
KnowledgeEdgeType = Literal[
    "has_prerequisite",
    "has_stage",
    "has_subdirection",
    "precedes",
    "requires",
    "references",
    "supported_by",
]

_ISSUE_DIMENSIONS: dict[str, QualityDimension] = {
    "structure_error": "structure",
    "invalid_paper": "paper_validity",
    "low_paper_relevance": "paper_relevance",
    "paper_context_mismatch": "paper_relevance",
    "missing_core_paper": "paper_relevance",
    "language_mismatch": "language_alignment",
    "generation_fallback": "structure",
    "missing_coverage": "topic_coverage",
    "weak_development_stage": "development_coherence",
    "route_conflict": "learning_path",
    # Kept for replaying legacy audit records. New standard-novice outputs no
    # longer emit personalization mismatch issues.
    "beginner_mismatch": "learning_path",
    "format_error": "structure",
    "missing_evidence": "evidence_grounding",
    "unsupported_claim": "evidence_grounding",
}
_HARD_GATE_ISSUES = {
    "structure_error",
    "invalid_paper",
    "format_error",
    "missing_evidence",
    "unsupported_claim",
    "low_paper_relevance",
    "paper_context_mismatch",
    "missing_core_paper",
    "language_mismatch",
}
_ISSUE_REPAIRABILITY: dict[str, Repairability] = {
    "structure_error": "llm",
    "invalid_paper": "code",
    "missing_coverage": "retrieval",
    "weak_development_stage": "llm",
    "route_conflict": "code",
    "beginner_mismatch": "llm",
    "format_error": "code",
    "missing_evidence": "llm",
    "unsupported_claim": "llm",
    "low_paper_relevance": "retrieval",
    "paper_context_mismatch": "retrieval",
    "missing_core_paper": "retrieval",
    "language_mismatch": "llm",
    "generation_fallback": "none",
}

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
    language: Literal["zh-CN", "en-US"] = "zh-CN"

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        query = value.strip()
        if not query:
            raise ValueError("query must not be empty")
        return query

    @model_validator(mode="after")
    def infer_language(self) -> "DomainOnboardingRequest":
        configured = str(self.metadata.get("language") or self.metadata.get("locale") or "")
        if configured.lower().startswith("en"):
            self.language = "en-US"
        elif configured.lower().startswith("zh") or re.search(r"[\u4e00-\u9fff]", self.query):
            self.language = "zh-CN"
        return self


class LearnerProfile(OnboardingModel):
    background: list[str] = Field(default_factory=list)
    goal: str = "建立领域基础认知并具备阅读代表论文的能力"
    time_budget_weeks: int | None = Field(default=None, ge=1, le=260)
    preference: Preference = "balanced"
    known_concepts: list[str] = Field(default_factory=list)


class ResearchPerspective(OnboardingModel):
    path_id: str = ""
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    questions: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)


class DevelopmentStageResearchPlan(OnboardingModel):
    stage_id: str = ""
    sequence: int = Field(ge=1)
    name: str = Field(min_length=1)
    period: str = Field(min_length=1)
    focus: str = Field(min_length=1)
    transition_from_previous: str = ""
    search_queries: list[str] = Field(min_length=1)
    selected_paper_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_stage_research(self) -> "DevelopmentStageResearchPlan":
        self.stage_id = self.stage_id or stable_id(
            "research-stage", f"{self.sequence}:{self.name}:{self.period}"
        )
        self.search_queries = list(
            dict.fromkeys(query.strip() for query in self.search_queries if query.strip())
        )
        self.selected_paper_ids = list(dict.fromkeys(self.selected_paper_ids))
        return self


class PaperSearchQuery(OnboardingModel):
    query_id: str = ""
    query: str = Field(min_length=1)
    role_hint: PaperRole
    path_id: str = ""
    priority: int = Field(default=3, ge=1, le=10)

    @model_validator(mode="after")
    def normalize_query(self) -> "PaperSearchQuery":
        self.query = self.query.strip()
        self.path_id = self.path_id.strip()
        self.query_id = self.query_id.strip() or stable_id(
            "query", f"{self.role_hint}:{self.path_id}:{self.query}"
        )
        return self


class DomainResearchPlan(OnboardingModel):
    normalized_domain: str = Field(min_length=1)
    translated_domain: str = ""
    expanded_terms: list[str] = Field(default_factory=list)
    perspectives: list[ResearchPerspective] = Field(min_length=3)
    search_queries: list[str] = Field(min_length=1)
    paper_queries: list[PaperSearchQuery] = Field(default_factory=list)
    expected_subdirections: list[str] = Field(min_length=3)
    development_stage_plans: list[DevelopmentStageResearchPlan] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def deduplicate(self) -> "DomainResearchPlan":
        self.translated_domain = self.translated_domain.strip()
        self.expanded_terms = list(
            dict.fromkeys(term.strip() for term in self.expanded_terms if term.strip())
        )
        used_path_ids: set[str] = set()
        for index, perspective in enumerate(self.perspectives, start=1):
            candidate_path_id = perspective.path_id.strip() or f"path-{index}"
            if candidate_path_id in used_path_ids:
                candidate_path_id = f"{candidate_path_id}-{index}"
            perspective.path_id = candidate_path_id
            used_path_ids.add(candidate_path_id)
            perspective.questions = list(
                dict.fromkeys(item.strip() for item in perspective.questions if item.strip())
            )
            perspective.search_queries = list(
                dict.fromkeys(
                    item.strip() for item in perspective.search_queries if item.strip()
                )
            )
        self.search_queries = list(dict.fromkeys(q.strip() for q in self.search_queries if q.strip()))
        seen_queries: set[str] = set()
        normalized_paper_queries: list[PaperSearchQuery] = []
        for query in self.paper_queries:
            key = query.query.casefold()
            if key in seen_queries:
                continue
            seen_queries.add(key)
            normalized_paper_queries.append(query)
        self.paper_queries = normalized_paper_queries
        self.expected_subdirections = list(
            dict.fromkeys(s.strip() for s in self.expected_subdirections if s.strip())
        )
        self.development_stage_plans = sorted(
            self.development_stage_plans, key=lambda stage: stage.sequence
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
    matched_role_hints: list[PaperRole] = Field(default_factory=list)
    matched_path_hints: list[str] = Field(default_factory=list)
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

    @field_validator("matched_queries", "matched_path_hints", mode="before")
    @classmethod
    def normalize_string_lists(cls, value: object) -> list[str]:
        values = value if isinstance(value, list) else ([value] if value else [])
        return list(
            dict.fromkeys(str(item).strip() for item in values if str(item).strip())
        )

    @field_validator("matched_role_hints", mode="before")
    @classmethod
    def normalize_role_hints(cls, value: object) -> list[str]:
        values = value if isinstance(value, list) else ([value] if value else [])
        return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))


class RankedPaper(PaperCandidate):
    relevance_score: float = Field(ge=0.0, le=1.0)
    context_score: float = Field(default=1.0, ge=0.0, le=1.0)
    recency_score: float = Field(ge=0.0, le=1.0)
    diversity_score: float = Field(ge=0.0, le=1.0)
    final_score: float = Field(ge=0.0, le=1.0)
    paper_role: PaperRole = "other"
    reading_priority: ReadingPriority = "optional"
    is_canonical: bool = False
    base_score: float = Field(default=0.0, ge=0.0, le=1.0)
    path_fusion_score: float = Field(default=0.0, ge=0.0, le=1.0)
    path_relevance_scores: dict[str, float] = Field(default_factory=dict)
    matched_research_paths: list[str] = Field(default_factory=list)


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
    reading_priority: ReadingPriority = "optional"
    is_canonical: bool = False
    relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    context_score: float = Field(default=1.0, ge=0.0, le=1.0)
    final_score: float = Field(default=0.0, ge=0.0, le=1.0)
    recency_score: float = Field(default=0.0, ge=0.0, le=1.0)
    diversity_score: float = Field(default=0.0, ge=0.0, le=1.0)
    contribution: str = ""
    reading_focus: list[str] = Field(default_factory=list)

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
    reading_focus: list[str] = Field(default_factory=list)
    reading_priority: ReadingPriority = "optional"
    is_canonical: bool = False


class ConceptDetail(OnboardingModel):
    """Beginner-facing concept explanation with explicit paper evidence."""

    concept_id: str | None = None
    name: str = Field(min_length=1)
    explanation: str = ""
    why_it_matters: str = ""
    related_paper_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def ensure_id(self) -> "ConceptDetail":
        self.concept_id = self.concept_id or stable_id("concept", self.name)
        self.related_paper_ids = list(dict.fromkeys(self.related_paper_ids))
        return self


class TechniqueDetail(OnboardingModel):
    """Beginner-facing technique explanation with mechanism and evidence."""

    technique_id: str | None = None
    name: str = Field(min_length=1)
    explanation: str = ""
    mechanism: str = ""
    why_it_matters: str = ""
    related_paper_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def ensure_id(self) -> "TechniqueDetail":
        self.technique_id = self.technique_id or stable_id("technique", self.name)
        self.related_paper_ids = list(dict.fromkeys(self.related_paper_ids))
        return self


def _detail_items(value: object, *, technique: bool = False) -> list[object]:
    """Accept legacy strings while preserving richer v1.7 detail objects."""

    values = value if isinstance(value, list) else ([value] if value else [])
    normalized: list[object] = []
    for item in values:
        if isinstance(item, (ConceptDetail, TechniqueDetail)):
            normalized.append(item)
            continue
        if isinstance(item, str):
            if item.strip():
                normalized.append({"name": item.strip()})
            continue
        if not isinstance(item, dict):
            continue
        detail = dict(item)
        detail["name"] = str(
            detail.get("name") or detail.get("title") or detail.get("label") or ""
        ).strip()
        if not detail["name"]:
            continue
        detail["explanation"] = str(
            detail.get("explanation") or detail.get("description") or ""
        ).strip()
        detail["why_it_matters"] = str(detail.get("why_it_matters") or "").strip()
        detail["related_paper_ids"] = detail.get(
            "related_paper_ids", detail.get("paper_ids", [])
        )
        if technique:
            detail["mechanism"] = str(detail.get("mechanism") or "").strip()
        normalized.append(detail)
    return normalized


class Prerequisite(OnboardingModel):
    prerequisite_id: str | None = None
    name: str
    why_needed: str = ""
    key_points: list[ConceptDetail] = Field(default_factory=list)
    related_paper_ids: list[str] = Field(default_factory=list)

    @field_validator("key_points", mode="before")
    @classmethod
    def normalize_key_points(cls, value: object) -> list[object]:
        return _detail_items(value)

    @model_validator(mode="after")
    def ensure_id(self) -> "Prerequisite":
        self.prerequisite_id = self.prerequisite_id or stable_id("pre", self.name)
        return self


class StageBreakthrough(OnboardingModel):
    breakthrough_id: str | None = None
    name: str
    description: str = ""
    supporting_paper_ids: list[str] = Field(default_factory=list)
    enabled_capabilities: list[str] = Field(default_factory=list)
    limitation_problem_ids: list[str] = Field(default_factory=list)
    relation_status: RelationStatus = "unresolved"

    @model_validator(mode="after")
    def ensure_id(self) -> "StageBreakthrough":
        self.breakthrough_id = self.breakthrough_id or stable_id(
            "breakthrough", self.name
        )
        self.supporting_paper_ids = list(dict.fromkeys(self.supporting_paper_ids))
        self.limitation_problem_ids = list(dict.fromkeys(self.limitation_problem_ids))
        return self


class DevelopmentStage(OnboardingModel):
    stage_id: str | None = None
    sequence: int = Field(default=1, ge=1)
    name: str
    period: str = ""
    historical_period: str = ""
    start_year: int | None = Field(default=None, ge=1800, le=2100)
    end_year: int | None = Field(default=None, ge=1800, le=2100)
    summary: str = ""
    motivation: str = ""
    previous_stage_id: str | None = None
    transition_from_previous: str = ""
    representative_papers: list[PaperReference] = Field(default_factory=list)
    core_concepts: list[ConceptDetail] = Field(default_factory=list)
    main_techniques: list[TechniqueDetail] = Field(default_factory=list)
    open_problems: list[str] = Field(default_factory=list)
    breakthroughs: list[StageBreakthrough] = Field(default_factory=list)
    related_problem_ids: list[str] = Field(default_factory=list)
    related_paper_ids: list[str] = Field(default_factory=list)
    prerequisite_ids: list[str] = Field(default_factory=list)

    @field_validator("core_concepts", mode="before")
    @classmethod
    def normalize_core_concepts(cls, value: object) -> list[object]:
        return _detail_items(value)

    @field_validator("main_techniques", mode="before")
    @classmethod
    def normalize_main_techniques(cls, value: object) -> list[object]:
        return _detail_items(value, technique=True)

    @model_validator(mode="after")
    def ensure_id(self) -> "DevelopmentStage":
        self.stage_id = self.stage_id or stable_id("stage", self.name)
        self.historical_period = self.historical_period or self.period
        self.period = self.historical_period
        self.related_problem_ids = list(dict.fromkeys(self.related_problem_ids))
        return self


class LandscapeProblem(OnboardingModel):
    problem_id: str | None = None
    name: str
    description: str = ""
    related_paper_ids: list[str] = Field(default_factory=list)
    related_stage_ids: list[str] = Field(default_factory=list)
    emerged_in_stage_id: str | None = None
    affected_stage_ids: list[str] = Field(default_factory=list)
    related_subdirection_ids: list[str] = Field(default_factory=list)
    relation_status: RelationStatus = "unresolved"

    @model_validator(mode="after")
    def ensure_id(self) -> "LandscapeProblem":
        self.problem_id = self.problem_id or stable_id("problem", self.name)
        self.related_paper_ids = list(dict.fromkeys(self.related_paper_ids))
        self.related_stage_ids = list(dict.fromkeys(self.related_stage_ids))
        self.affected_stage_ids = list(dict.fromkeys(self.affected_stage_ids))
        self.related_subdirection_ids = list(dict.fromkeys(self.related_subdirection_ids))
        return self


class SubdirectionDetail(OnboardingModel):
    subdirection_id: str | None = None
    name: str
    description: str = ""
    why_it_matters: str = ""
    typical_tasks: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    common_techniques: list[TechniqueDetail] = Field(default_factory=list)
    datasets_and_benchmarks: list[str] = Field(default_factory=list)
    evaluation_metrics: list[str] = Field(default_factory=list)
    starter_project: str = ""
    research_workflow: list[str] = Field(default_factory=list)
    research_questions: list[str] = Field(default_factory=list)
    related_paper_ids: list[str] = Field(default_factory=list)
    related_stage_ids: list[str] = Field(default_factory=list)
    emerged_in_stage_id: str | None = None
    addresses_problem_ids: list[str] = Field(default_factory=list)
    relation_status: RelationStatus = "unresolved"

    @field_validator("common_techniques", mode="before")
    @classmethod
    def normalize_common_techniques(cls, value: object) -> list[object]:
        return _detail_items(value, technique=True)

    @model_validator(mode="after")
    def ensure_id(self) -> "SubdirectionDetail":
        self.subdirection_id = self.subdirection_id or stable_id("sub", self.name)
        self.related_paper_ids = list(dict.fromkeys(self.related_paper_ids))
        self.related_stage_ids = list(dict.fromkeys(self.related_stage_ids))
        self.addresses_problem_ids = list(dict.fromkeys(self.addresses_problem_ids))
        return self


class CurrentLandscape(OnboardingModel):
    problems: list[str] = Field(default_factory=list)
    subdirections: list[str] = Field(default_factory=list)
    subdirection_ids: dict[str, str] = Field(default_factory=dict)
    problem_details: list[LandscapeProblem] = Field(default_factory=list)
    subdirection_details: list[SubdirectionDetail] = Field(default_factory=list)

    @model_validator(mode="after")
    def ensure_ids(self) -> "CurrentLandscape":
        if not self.problems and self.problem_details:
            self.problems = [item.name for item in self.problem_details]
        if not self.subdirections and self.subdirection_details:
            self.subdirections = [item.name for item in self.subdirection_details]
        self.subdirection_ids = {
            name: self.subdirection_ids.get(name)
            or next(
                (
                    str(item.subdirection_id)
                    for item in self.subdirection_details
                    if item.name == name
                ),
                stable_id("sub", name),
            )
            for name in self.subdirections
        }
        return self


class LearningPaperBinding(OnboardingModel):
    paper_id: str
    learning_use: LearningUse
    reason: str = Field(min_length=1)
    reading_mode: ReadingMode = "read"
    required: bool = True
    binding_status: BindingStatus = "policy_matched"
    matched_signals: list[str] = Field(default_factory=list)


class LearningStep(OnboardingModel):
    learning_step_id: str | None = None
    step: str
    goal: str = ""
    topics: list[str] = Field(default_factory=list)
    papers: list[PaperReference] = Field(default_factory=list)
    paper_ids: list[str] = Field(default_factory=list)
    activities: list[str] = Field(default_factory=list)
    completion_criteria: list[str] = Field(default_factory=list)
    expected_outcome: str = ""
    start_week: int | None = Field(default=None, ge=1)
    end_week: int | None = Field(default=None, ge=1)
    estimated_hours: int | None = Field(default=None, ge=1)
    milestone: str = ""
    deliverables: list[str] = Field(default_factory=list)
    reproducibility_checklist: list[str] = Field(default_factory=list)
    evaluation_metrics: list[str] = Field(default_factory=list)
    paper_bindings: list[LearningPaperBinding] = Field(default_factory=list)
    related_stage_ids: list[str] = Field(default_factory=list)
    related_problem_ids: list[str] = Field(default_factory=list)
    related_subdirection_ids: list[str] = Field(default_factory=list)

    @field_validator("step", mode="before")
    @classmethod
    def stringify_step(cls, value: Any) -> str:
        return str(value).strip()

    @model_validator(mode="after")
    def ensure_id(self) -> "LearningStep":
        self.learning_step_id = self.learning_step_id or stable_id(
            "learning-step", f"{self.step}:{self.goal}"
        )
        self.related_stage_ids = list(dict.fromkeys(self.related_stage_ids))
        self.related_problem_ids = list(dict.fromkeys(self.related_problem_ids))
        self.related_subdirection_ids = list(
            dict.fromkeys(self.related_subdirection_ids)
        )
        return self


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
    schema_version: str = "domain-onboarding-output-v1.9"
    language: Literal["zh-CN", "en-US"] = "zh-CN"
    domain: str
    text: str
    learner_profile: LearnerProfile
    research_plan: DomainResearchPlan | None = None
    prerequisites: list[Prerequisite]
    development_stages: list[DevelopmentStage]
    current_landscape: CurrentLandscape
    learning_path: list[LearningStep]
    papers: list[SelectedPaper]
    evidence_claims: list[EvidenceClaim] = Field(default_factory=list)
    reproducibility: dict[str, Any] = Field(default_factory=dict)


class QualityIssue(OnboardingModel):
    issue_id: str | None = None
    issue_type: QualityIssueType
    severity: Literal["warning", "error", "critical"]
    dimension: QualityDimension | None = None
    hard_gate: bool | None = None
    repairability: Repairability | None = None
    target_path: str
    message: str
    recommended_action: str

    @model_validator(mode="after")
    def enrich_issue(self) -> "QualityIssue":
        identity = f"{self.issue_type}:{self.target_path}:{self.message}"
        self.issue_id = self.issue_id or stable_id("issue", identity)
        self.dimension = self.dimension or _ISSUE_DIMENSIONS[self.issue_type]
        if self.hard_gate is None:
            self.hard_gate = (
                self.issue_type in _HARD_GATE_ISSUES
                and self.severity in {"error", "critical"}
            )
        self.repairability = self.repairability or _ISSUE_REPAIRABILITY[self.issue_type]
        return self


class QualityGateResult(OnboardingModel):
    gate: str = Field(min_length=1)
    status: QualityGateStatus
    issue_ids: list[str] = Field(default_factory=list)
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("issue_ids")
    @classmethod
    def deduplicate_issue_ids(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value for value in values if value))


class ContentQuality(OnboardingModel):
    policy_version: str = "domain-quality-v1.5.0"
    policy_fingerprint: str | None = None
    score: float = Field(ge=0.0, le=1.0)
    threshold: float = Field(ge=0.0, le=1.0)
    passed_hard_gates: bool
    dimensions: dict[str, float]
    issues: list[QualityIssue] = Field(default_factory=list)
    attempts: int = Field(default=1, ge=1, le=2)
    selected_attempt: int = Field(default=1, ge=1, le=2)
    retry_status: RetryStatus = "not_needed"
    state: QualityState = "warning"
    hard_gates: list[QualityGateResult] = Field(default_factory=list)
    evidence_validation_modes: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def derive_state(self) -> "ContentQuality":
        if not self.passed_hard_gates:
            self.state = "failed"
        elif self.score >= self.threshold and not self.issues:
            self.state = "passed"
        else:
            self.state = "warning"
        return self


class QualityAttempt(OnboardingModel):
    attempt_number: int = Field(ge=1, le=2)
    source: QualityAttemptSource
    quality: ContentQuality
    duration_ms: float = Field(default=0.0, ge=0.0)


class FinalQualitySummary(OnboardingModel):
    verdict: QualityState
    initial_score: float = Field(ge=0.0, le=1.0)
    final_score: float = Field(ge=0.0, le=1.0)
    score_delta: float = Field(ge=-1.0, le=1.0)
    threshold: float = Field(ge=0.0, le=1.0)
    selected_attempt: int = Field(ge=1, le=2)
    repair_applied: bool = False
    selection_reason: str = "quality_threshold_met"
    passed_hard_gates: bool
    hard_gate_pass_count: int = Field(ge=0)
    hard_gate_total: int = Field(ge=0)
    unresolved_issue_count: int = Field(ge=0)
    issue_counts_by_severity: dict[str, int] = Field(default_factory=dict)
    dimension_deltas: dict[str, float] = Field(default_factory=dict)


class RepairActionRecord(OnboardingModel):
    action_id: str
    action_type: RepairActionType
    status: RepairActionStatus
    issue_ids: list[str] = Field(default_factory=list)
    target_paths: list[str] = Field(default_factory=list)
    changed_paths: list[str] = Field(default_factory=list)
    before_fingerprint: str | None = None
    after_fingerprint: str | None = None
    error: str | None = None


class RepairDecision(OnboardingModel):
    selected_attempt: int = Field(ge=1, le=2)
    decision: RepairSelection
    reasons: list[RepairDecisionReason] = Field(default_factory=list)
    score_delta: float = 0.0
    dimension_deltas: dict[str, float] = Field(default_factory=dict)


class RepairRecord(OnboardingModel):
    policy_version: str = "domain-quality-v1.5.0"
    policy_fingerprint: str | None = None
    adaptive_policy_version: str | None = None
    shadow_recommendations: dict[QualityIssueType, RepairActionType] = Field(
        default_factory=dict
    )
    triggered: bool = False
    actions: list[RepairActionRecord] = Field(default_factory=list)
    decision: RepairDecision | None = None


class RepairPlan(OnboardingModel):
    actions: list[RepairActionRecord] = Field(default_factory=list)


class KnowledgeGraphNode(OnboardingModel):
    node_id: str
    node_type: KnowledgeNodeType
    label: str
    source_path: str
    paper_id: str | None = None


class KnowledgeGraphEdge(OnboardingModel):
    source_id: str
    target_id: str
    edge_type: KnowledgeEdgeType
    source_path: str


class GraphValidationIssue(OnboardingModel):
    issue_type: Literal[
        "duplicate_node", "dangling_edge", "unknown_paper", "dependency_cycle",
        "malformed_label",
    ]
    message: str
    target_id: str | None = None


class GraphValidationReport(OnboardingModel):
    valid: bool
    issues: list[GraphValidationIssue] = Field(default_factory=list)


class GraphPathPlan(OnboardingModel):
    ordered_node_ids: list[str] = Field(default_factory=list)
    fallback_used: bool = False
    reason: str | None = None


class KnowledgeGraphSnapshot(OnboardingModel):
    graph_schema_version: str = "1.0"
    request_id: str
    quality_policy_version: str
    selected_paper_ids: list[str] = Field(default_factory=list)
    nodes: list[KnowledgeGraphNode] = Field(default_factory=list)
    edges: list[KnowledgeGraphEdge] = Field(default_factory=list)
    validation: GraphValidationReport
    path_plan: GraphPathPlan | None = None


class PipelineResult(OnboardingModel):
    policy_version: str = "domain-quality-v1.5.0"
    policy_fingerprint: str | None = None
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
    quality_attempts: list[QualityAttempt] = Field(default_factory=list)
    final_quality: FinalQualitySummary | None = None
    repair_record: RepairRecord | None = None
    knowledge_graph: KnowledgeGraphSnapshot | None = None
    error: str | None = None

    @model_validator(mode="after")
    def validate_quality_audit_consistency(self) -> "PipelineResult":
        if self.quality is None:
            return self
        if self.final_quality is not None and (
            self.final_quality.final_score != self.quality.score
            or self.final_quality.verdict != self.quality.state
            or self.final_quality.selected_attempt != self.quality.selected_attempt
        ):
            raise ValueError("final quality summary must match selected quality")
        if self.quality.policy_version != self.policy_version:
            raise ValueError("result and quality policy versions must match")
        if self.quality.policy_fingerprint != self.policy_fingerprint:
            raise ValueError("result and quality policy fingerprints must match")
        selected = next(
            (
                attempt.quality
                for attempt in self.quality_attempts
                if attempt.attempt_number == self.quality.selected_attempt
            ),
            None,
        )
        if selected is None:
            raise ValueError("selected quality attempt is missing")
        fields = (
            "policy_version",
            "policy_fingerprint",
            "score",
            "threshold",
            "passed_hard_gates",
            "dimensions",
            "issues",
            "state",
            "hard_gates",
            "evidence_validation_modes",
        )
        if any(getattr(selected, field) != getattr(self.quality, field) for field in fields):
            raise ValueError("selected quality attempt must match final quality")
        if self.repair_record is not None:
            if self.repair_record.policy_version != self.policy_version:
                raise ValueError("result and repair policy versions must match")
            if self.repair_record.policy_fingerprint != self.policy_fingerprint:
                raise ValueError("result and repair policy fingerprints must match")
        return self

    def to_response(self) -> dict[str, Any]:
        if self.output is None:
            return self.model_dump(mode="json", exclude_none=True)
        payload = self.output.model_dump(mode="json")
        payload.update(
            status=self.status,
            mode=self.mode,
            query=self.query,
            policy_version=self.policy_version,
            policy_fingerprint=self.policy_fingerprint,
            quality=self.quality.model_dump(mode="json") if self.quality else None,
            quality_attempts=[
                attempt.model_dump(mode="json") for attempt in self.quality_attempts
            ],
            final_quality=(
                self.final_quality.model_dump(mode="json")
                if self.final_quality
                else None
            ),
            repair_record=(
                self.repair_record.model_dump(mode="json")
                if self.repair_record
                else None
            ),
        )
        if self.knowledge_graph:
            payload["knowledge_graph"] = self.knowledge_graph.model_dump(mode="json")
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
    covered_roles: list[PaperRole] = Field(default_factory=list)
    missing_required_roles: list[PaperRole] = Field(default_factory=list)
    ranking_strategy: str = "global"
    per_path_candidate_counts: dict[str, int] = Field(default_factory=dict)
    selected_path_counts: dict[str, int] = Field(default_factory=dict)
    per_role_candidate_counts: dict[PaperRole, int] = Field(default_factory=dict)
    selected_role_counts: dict[PaperRole, int] = Field(default_factory=dict)


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
    record: RepairRecord = Field(default_factory=RepairRecord)
