from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from handlers.domain_onboarding.policy import default_dimension_weights
from handlers.domain_onboarding.schemas import QualityDimension, QualityIssueType


class EvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ObservedQuality(EvaluationModel):
    score: float = Field(ge=0.0, le=1.0)
    passed_hard_gates: bool
    dimensions: dict[QualityDimension, float]
    issue_types: list[QualityIssueType] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_dimensions(self) -> "ObservedQuality":
        _validate_dimensions(self.dimensions)
        return self


class HumanQualityAnnotation(EvaluationModel):
    expected_hard_gate_pass: bool
    expected_issue_types: list[QualityIssueType] = Field(default_factory=list)
    dimension_scores: dict[QualityDimension, float]
    annotator: str = Field(min_length=1)
    annotation_version: str = Field(min_length=1)
    annotation_status: Literal["seed", "human_verified"] = "seed"

    @model_validator(mode="after")
    def validate_dimensions(self) -> "HumanQualityAnnotation":
        _validate_dimensions(self.dimension_scores)
        return self


class OfflineEvaluationCase(EvaluationModel):
    case_id: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    language: Literal["zh", "en", "bilingual"]
    policy_version: str = Field(min_length=1)
    first: ObservedQuality
    retry: ObservedQuality | None = None
    selected_attempt: Literal[1, 2] = 1
    repeated_dimensions: list[dict[QualityDimension, float]] = Field(
        default_factory=list
    )
    human: HumanQualityAnnotation

    @model_validator(mode="after")
    def validate_attempts(self) -> "OfflineEvaluationCase":
        if self.selected_attempt == 2 and self.retry is None:
            raise ValueError("selected_attempt=2 requires retry quality")
        for dimensions in self.repeated_dimensions:
            _validate_dimensions(dimensions)
        return self


class DomainEvaluationSummary(EvaluationModel):
    cases: int
    hard_gate_pass_rate: float
    hard_gate_agreement_rate: float
    false_positive_rate: float


class PolicyEvaluationSummary(DomainEvaluationSummary):
    repair_attempt_count: int
    repair_improvement_rate: float
    dimension_mean_absolute_error: dict[str, float]
    dimension_stability_range: dict[str, float]


class OfflineEvaluationReport(EvaluationModel):
    dataset_version: str
    policy_versions: list[str]
    case_count: int
    annotation_status_counts: dict[str, int]
    hard_gate_pass_rate: float
    hard_gate_agreement_rate: float
    repair_attempt_count: int
    repair_improved_count: int
    repair_improvement_rate: float
    false_positive_count: int
    predicted_issue_count: int
    false_positive_rate: float
    dimension_mean_absolute_error: dict[str, float]
    dimension_stability_range: dict[str, float]
    policy_summaries: dict[str, PolicyEvaluationSummary]
    domain_summaries: dict[str, DomainEvaluationSummary]


def _validate_dimensions(dimensions: dict[QualityDimension, float]) -> None:
    expected = set(default_dimension_weights())
    supported = {
        frozenset(expected),
        frozenset(expected - {"language_alignment"}),
        frozenset(expected - {"paper_relevance", "language_alignment"}),
    }
    if frozenset(dimensions) not in supported:
        raise ValueError("all quality dimensions for the declared policy must be present")
    if any(value < 0.0 or value > 1.0 for value in dimensions.values()):
        raise ValueError("quality dimension values must be between 0 and 1")
