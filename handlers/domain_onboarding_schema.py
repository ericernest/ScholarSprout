"""定义领域入门模型输出及成功响应的数据结构。"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


def _repair_text(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return value


def _ensure_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _repair_named_list(value: Any, name_field: str) -> list[Any]:
    repaired: list[Any] = []
    for item in _ensure_list(value):
        if isinstance(item, (str, int, float)) and not isinstance(item, bool):
            repaired.append({name_field: item})
        else:
            repaired.append(item)
    return repaired


def _repair_prerequisites(value: Any) -> list[Any]:
    return _repair_named_list(value, "name")


def _repair_development_stages(value: Any) -> list[Any]:
    return _repair_named_list(value, "name")


def _repair_learning_path(value: Any) -> list[Any]:
    return _repair_named_list(value, "step")


def _repair_papers(value: Any) -> list[Any]:
    return _repair_named_list(value, "title")


Text = Annotated[str, BeforeValidator(_repair_text)]
RequiredText = Annotated[str, BeforeValidator(_repair_text), Field(min_length=1)]
TextList = Annotated[list[Text], BeforeValidator(_ensure_list)]
AttemptNumber = Literal[1, 2]
RetryStatus = Literal[
    "not_needed",
    "improved",
    "not_improved",
    "invalid_response",
    "llm_failed",
]


class DomainOnboardingModel(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class PaperReference(DomainOnboardingModel):
    title: RequiredText
    authors: TextList = Field(default_factory=list)
    year: Annotated[int, Field(ge=1900, le=2100)] | None = None
    contribution: Text = ""

    @model_validator(mode="before")
    @classmethod
    def repair_aliases(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        repaired = dict(value)
        if not repaired.get("title"):
            for alias in ("name", "paper"):
                if repaired.get(alias):
                    repaired["title"] = repaired[alias]
                    break
        if not repaired.get("contribution"):
            for alias in ("why_representative", "summary"):
                if repaired.get(alias):
                    repaired["contribution"] = repaired[alias]
                    break
        return repaired

    @field_validator("year", mode="before")
    @classmethod
    def repair_year(cls, value: Any) -> Any:
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            raise ValueError("year must be a four-digit year")
        if isinstance(value, str):
            match = re.fullmatch(r"\s*(\d{4})\s*年?\s*", value)
            if match:
                return int(match.group(1))
        return value


PaperList = Annotated[list[PaperReference], BeforeValidator(_repair_papers)]


class Prerequisite(DomainOnboardingModel):
    name: RequiredText
    why_needed: Text = ""
    key_points: TextList = Field(default_factory=list)


class DevelopmentStage(DomainOnboardingModel):
    name: RequiredText
    summary: Text = ""
    motivation: Text = ""
    representative_papers: PaperList = Field(default_factory=list)
    core_concepts: TextList = Field(default_factory=list)
    main_techniques: TextList = Field(default_factory=list)
    open_problems: TextList = Field(default_factory=list)


class CurrentLandscape(DomainOnboardingModel):
    problems: TextList = Field(default_factory=list)
    subdirections: TextList = Field(default_factory=list)


class LearningStep(DomainOnboardingModel):
    step: RequiredText
    goal: Text = ""
    topics: TextList = Field(default_factory=list)
    papers: PaperList = Field(default_factory=list)
    expected_outcome: Text = ""


PrerequisiteList = Annotated[
    list[Prerequisite],
    BeforeValidator(_repair_prerequisites),
]
DevelopmentStageList = Annotated[
    list[DevelopmentStage],
    BeforeValidator(_repair_development_stages),
]
LearningPath = Annotated[
    list[LearningStep],
    BeforeValidator(_repair_learning_path),
]


class DomainOnboardingOutput(DomainOnboardingModel):
    domain: Text = ""
    text: Text = ""
    prerequisites: PrerequisiteList = Field(default_factory=list)
    development_stages: DevelopmentStageList = Field(default_factory=list)
    current_landscape: CurrentLandscape = Field(default_factory=CurrentLandscape)
    learning_path: LearningPath = Field(default_factory=list)

    @field_validator("current_landscape", mode="before")
    @classmethod
    def repair_current_landscape(cls, value: Any) -> Any:
        if value is None or value == "":
            return {}
        return value


class ContentQuality(DomainOnboardingModel):
    score: Annotated[int, Field(ge=0, le=100)]
    threshold: Annotated[int, Field(ge=0, le=100)]
    level: Literal["high", "acceptable", "low"]
    issues: list[str] = Field(default_factory=list)
    attempts: AttemptNumber = 1
    selected_attempt: AttemptNumber = 1
    retry_status: RetryStatus = "not_needed"


class DomainOnboardingSuccessResult(DomainOnboardingOutput):
    status: Literal["ok"] = "ok"
    mode: Literal["domain_onboarding"] = "domain_onboarding"
    query: RequiredText
    quality: ContentQuality
