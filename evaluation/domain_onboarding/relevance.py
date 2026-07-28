"""Paper-level relevance annotations and deterministic ranking metrics."""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RelevanceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RelevancePaper(RelevanceModel):
    paper_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    year: int = Field(ge=1800, le=2100)
    role: Literal["survey", "foundational", "method", "evaluation", "frontier", "other"]
    relevance_grade: int = Field(ge=0, le=3)
    rationale: str = Field(min_length=1)


class RelevanceAnnotationCase(RelevanceModel):
    case_id: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    normalized_domain: str = Field(min_length=1)
    language: Literal["zh", "en", "bilingual"] = "bilingual"
    query: str = Field(min_length=1)
    annotation_version: str = Field(min_length=1)
    annotation_status: Literal["seed", "human_verified"] = "seed"
    papers: list[RelevancePaper] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_papers(self) -> "RelevanceAnnotationCase":
        identifiers = [paper.paper_id for paper in self.papers]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("paper_id values must be unique within a case")
        if not any(paper.relevance_grade >= 2 for paper in self.papers):
            raise ValueError("each case requires at least one relevant paper")
        if not any(paper.relevance_grade == 0 for paper in self.papers):
            raise ValueError("each case requires at least one negative paper")
        return self


class RelevanceDatasetSummary(RelevanceModel):
    dataset_version: str
    case_count: int
    paper_count: int
    domain_count: int
    annotation_status_counts: dict[str, int]
    grade_counts: dict[str, int]
    role_counts: dict[str, int]
    relevant_paper_rate: float


def load_relevance_annotations(path: str | Path) -> list[RelevanceAnnotationCase]:
    source = Path(path)
    cases: list[RelevanceAnnotationCase] = []
    seen: set[str] = set()
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = RelevanceAnnotationCase.model_validate(json.loads(line))
        except Exception as error:
            raise ValueError(f"invalid relevance case at {source}:{line_number}: {error}") from error
        if case.case_id in seen:
            raise ValueError(f"duplicate relevance case_id: {case.case_id}")
        seen.add(case.case_id)
        cases.append(case)
    if not cases:
        raise ValueError(f"relevance dataset is empty: {source}")
    return cases


def summarize_relevance_annotations(
    cases: list[RelevanceAnnotationCase],
    *,
    dataset_version: str = "domain-paper-relevance-v1",
) -> RelevanceDatasetSummary:
    papers = [paper for case in cases for paper in case.papers]
    relevant = sum(paper.relevance_grade >= 2 for paper in papers)
    return RelevanceDatasetSummary(
        dataset_version=dataset_version,
        case_count=len(cases),
        paper_count=len(papers),
        domain_count=len({case.domain for case in cases}),
        annotation_status_counts=dict(sorted(Counter(case.annotation_status for case in cases).items())),
        grade_counts=dict(sorted(Counter(str(paper.relevance_grade) for paper in papers).items())),
        role_counts=dict(sorted(Counter(paper.role for paper in papers).items())),
        relevant_paper_rate=round(relevant / len(papers), 6) if papers else 0.0,
    )


def precision_at_k(ranked_ids: list[str], grades: dict[str, int], k: int) -> float:
    selected = ranked_ids[:k]
    return round(sum(grades.get(paper_id, 0) >= 2 for paper_id in selected) / len(selected), 6) if selected else 0.0


def ndcg_at_k(ranked_ids: list[str], grades: dict[str, int], k: int) -> float:
    def dcg(values: list[int]) -> float:
        return sum((2**grade - 1) / math.log2(index + 2) for index, grade in enumerate(values))

    observed = dcg([grades.get(paper_id, 0) for paper_id in ranked_ids[:k]])
    ideal = dcg(sorted(grades.values(), reverse=True)[:k])
    return round(observed / ideal, 6) if ideal else 0.0
