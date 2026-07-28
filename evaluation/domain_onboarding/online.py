"""Explicitly gated online evaluation against real retrieval and model services."""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from statistics import fmean
from time import perf_counter
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from handlers.domain_onboarding.metrics import DomainOnboardingRequestTrace
from handlers.domain_onboarding.schemas import (
    ContentQuality,
    DomainOnboardingRequest,
    QualityAttempt,
    RepairRecord,
)


class OnlineModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OnlineEvaluationCase(OnlineModel):
    case_id: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    language: Literal["zh", "en"]
    query: str = Field(min_length=1)


class OnlineRunLimits(OnlineModel):
    max_cases: int = Field(default=2, ge=1, le=12)
    max_estimated_cost_usd: float = Field(default=0.5, gt=0.0, le=20.0)
    cost_reserve_per_case_usd: float = Field(default=0.25, gt=0.0, le=20.0)


class OnlineCaseResult(OnlineModel):
    case_id: str
    domain: str
    language: Literal["zh", "en"]
    status: str
    duration_ms: float
    total_tokens: int
    usage_complete: bool
    estimated_cost_usd: float | None = None
    selected_paper_count: int
    valid_paper_count: int
    hard_gate_passed: bool | None = None
    cross_language_warning_count: int = 0
    interrupted_stage: str | None = None
    stage_durations_ms: dict[str, float] = Field(default_factory=dict)
    quality: ContentQuality | None = None
    quality_attempts: list[QualityAttempt] = Field(default_factory=list)
    repair_record: RepairRecord | None = None


class OnlineEvaluationReport(OnlineModel):
    run_schema_version: str = "1.0"
    policy_versions: list[str]
    requested_case_count: int
    completed_case_count: int
    budget_exhausted: bool
    statuses: dict[str, int]
    success_rate: float
    paper_validity_rate: float
    hard_gate_pass_rate: float
    average_duration_ms: float
    p50_duration_ms: float
    p95_duration_ms: float
    total_tokens: int
    usage_complete: bool
    estimated_cost_usd: float | None = None
    cross_language_warning_rate: float
    cases: list[OnlineCaseResult]


def load_online_cases(path: str | Path) -> list[OnlineEvaluationCase]:
    source = Path(path)
    cases = [
        OnlineEvaluationCase.model_validate(json.loads(line))
        for line in source.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not cases:
        raise ValueError(f"online evaluation dataset is empty: {source}")
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("online evaluation case_id values must be unique")
    return cases


def validate_online_permission(
    *,
    confirmed: bool,
    input_cost_per_million_tokens: float | None,
    output_cost_per_million_tokens: float | None,
    allow_unpriced: bool = False,
) -> None:
    if os.getenv("RUN_DOMAIN_ONBOARDING_ONLINE") != "1":
        raise RuntimeError("set RUN_DOMAIN_ONBOARDING_ONLINE=1 to enable real online calls")
    if not confirmed:
        raise RuntimeError("pass --confirm-online to acknowledge real network and model cost")
    if (
        input_cost_per_million_tokens is None
        or output_cost_per_million_tokens is None
    ) and not allow_unpriced:
        raise RuntimeError("model pricing is required unless --allow-unpriced is explicit")


def run_online_evaluation(
    pipeline: Any,
    cases: list[OnlineEvaluationCase],
    limits: OnlineRunLimits,
    *,
    input_cost_per_million_tokens: float | None,
    output_cost_per_million_tokens: float | None,
) -> OnlineEvaluationReport:
    selected = cases[: limits.max_cases]
    results: list[OnlineCaseResult] = []
    spent = 0.0
    budget_exhausted = False
    policy_versions: set[str] = set()
    for case in selected:
        if spent + limits.cost_reserve_per_case_usd > limits.max_estimated_cost_usd:
            budget_exhausted = True
            break
        trace = DomainOnboardingRequestTrace()
        started = perf_counter()
        try:
            result = pipeline.run(DomainOnboardingRequest(query=case.query), trace)
        except Exception:
            results.append(
                OnlineCaseResult(
                    case_id=case.case_id,
                    domain=case.domain,
                    language=case.language,
                    status="runner_error",
                    duration_ms=round((perf_counter() - started) * 1000, 3),
                    total_tokens=0,
                    usage_complete=False,
                    selected_paper_count=0,
                    valid_paper_count=0,
                )
            )
            continue
        duration_ms = round((perf_counter() - started) * 1000, 3)
        trace.total_duration_ms = duration_ms
        policy_versions.add(result.policy_version)
        total_calls = trace.first_model_calls + trace.retry_model_calls
        unreported = (
            trace.first_unreported_usage_calls + trace.retry_unreported_usage_calls
        )
        usage_complete = total_calls > 0 and unreported == 0
        prompt_tokens = trace.first_usage.prompt_tokens + trace.retry_usage.prompt_tokens
        completion_tokens = (
            trace.first_usage.completion_tokens + trace.retry_usage.completion_tokens
        )
        total_tokens = trace.first_usage.total_tokens + trace.retry_usage.total_tokens
        estimated_cost = _estimate_cost(
            prompt_tokens,
            completion_tokens,
            usage_complete,
            input_cost_per_million_tokens,
            output_cost_per_million_tokens,
        )
        if estimated_cost is not None:
            spent += estimated_cost
        papers = result.output.papers if result.output is not None else []
        warnings = 0
        if result.quality is not None:
            warnings = sum(
                issue.issue_type == "unsupported_claim"
                and issue.severity == "warning"
                for issue in result.quality.issues
            )
        results.append(
            OnlineCaseResult(
                case_id=case.case_id,
                domain=case.domain,
                language=case.language,
                status=result.status,
                duration_ms=duration_ms,
                total_tokens=total_tokens,
                usage_complete=usage_complete,
                estimated_cost_usd=estimated_cost,
                selected_paper_count=len(papers),
                valid_paper_count=sum(_valid_paper(paper) for paper in papers),
                hard_gate_passed=(
                    result.quality.passed_hard_gates
                    if result.quality is not None
                    else None
                ),
                cross_language_warning_count=warnings,
                interrupted_stage=trace.interrupted_stage,
                stage_durations_ms={
                    stage: round(float(getattr(trace, f"{stage}_duration_ms")), 3)
                    for stage in (
                        "profile", "planning", "retrieval", "ranking",
                        "generation", "evaluation", "repair",
                    )
                    if float(getattr(trace, f"{stage}_duration_ms")) > 0
                },
                quality=result.quality,
                quality_attempts=result.quality_attempts,
                repair_record=result.repair_record,
            )
        )
        if spent > limits.max_estimated_cost_usd:
            budget_exhausted = True
            break
    return _summarize(
        results,
        requested_case_count=len(selected),
        policy_versions=policy_versions,
        budget_exhausted=budget_exhausted,
    )


def write_online_report(report: OnlineEvaluationReport, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _summarize(
    results: list[OnlineCaseResult],
    *,
    requested_case_count: int,
    policy_versions: set[str],
    budget_exhausted: bool,
) -> OnlineEvaluationReport:
    statuses = Counter(result.status for result in results)
    paper_count = sum(result.selected_paper_count for result in results)
    valid_papers = sum(result.valid_paper_count for result in results)
    evaluated_gates = [
        result.hard_gate_passed
        for result in results
        if result.hard_gate_passed is not None
    ]
    durations = sorted(result.duration_ms for result in results)
    warning_cases = sum(result.cross_language_warning_count > 0 for result in results)
    costs = [result.estimated_cost_usd for result in results]
    cost_complete = bool(results) and all(cost is not None for cost in costs)
    return OnlineEvaluationReport(
        policy_versions=sorted(policy_versions),
        requested_case_count=requested_case_count,
        completed_case_count=len(results),
        budget_exhausted=budget_exhausted,
        statuses=dict(statuses),
        success_rate=_ratio(
            sum(result.status in {"ok", "quality_warning"} for result in results),
            len(results),
        ),
        paper_validity_rate=_ratio(valid_papers, paper_count),
        hard_gate_pass_rate=_ratio(sum(bool(value) for value in evaluated_gates), len(evaluated_gates)),
        average_duration_ms=round(fmean(durations), 3) if durations else 0.0,
        p50_duration_ms=_percentile(durations, 0.5),
        p95_duration_ms=_percentile(durations, 0.95),
        total_tokens=sum(result.total_tokens for result in results),
        usage_complete=bool(results) and all(result.usage_complete for result in results),
        estimated_cost_usd=(
            round(sum(float(cost) for cost in costs), 8) if cost_complete else None
        ),
        cross_language_warning_rate=_ratio(warning_cases, len(results)),
        cases=results,
    )


def _estimate_cost(
    prompt_tokens: int,
    completion_tokens: int,
    usage_complete: bool,
    input_price: float | None,
    output_price: float | None,
) -> float | None:
    if not usage_complete or input_price is None or output_price is None:
        return None
    return round(
        prompt_tokens / 1_000_000 * input_price
        + completion_tokens / 1_000_000 * output_price,
        8,
    )


def _valid_paper(paper: Any) -> bool:
    parsed = urlparse(str(paper.url))
    return bool(
        str(paper.paper_id).strip()
        and str(paper.title).strip()
        and str(paper.source).strip()
        and paper.year is not None
        and 1800 <= int(paper.year) <= 2100
        and parsed.scheme in {"http", "https"}
        and parsed.netloc
    )


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    index = max(0, min(len(values) - 1, int((len(values) * fraction) + 0.999999) - 1))
    return round(values[index], 3)


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0
