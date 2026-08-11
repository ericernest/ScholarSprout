"""Repeatable multi-model benchmark helpers for domain onboarding."""

from __future__ import annotations

import csv
import json
import random
from collections import defaultdict
from pathlib import Path
from statistics import fmean, median
from time import perf_counter
from typing import Any, Literal

from pydantic import Field

from handlers.domain_onboarding.metrics import DomainOnboardingRequestTrace
from handlers.domain_onboarding.schemas import (
    CoverageAnalysis,
    DomainOnboardingRequest,
    DomainResearchPlan,
    ModelCallStats,
    PlanningResult,
    RankedPaper,
    RankingResult,
    RankingStats,
    RetrievalResult,
    RetrievalStats,
)

from .online import OnlineEvaluationCase, OnlineModel


BenchmarkMode = Literal["end_to_end", "model_only"]
VISIBLE_SECTION_EVENTS = (
    "development_ready",
    "landscape_ready",
    "learning_path_ready",
)


class FrozenBenchmarkCase(OnlineModel):
    case: OnlineEvaluationCase
    plan: DomainResearchPlan
    papers: list[RankedPaper] = Field(min_length=1)


class ModelBenchmarkRun(OnlineModel):
    run_key: str
    mode: BenchmarkMode
    model: str
    case_id: str
    domain: str
    repeat: int = Field(ge=1)
    status: str
    generation_succeeded: bool
    delivery_succeeded: bool
    error: str | None = None
    duration_ms: float = Field(ge=0.0)
    first_llm_delta_ms: float | None = Field(default=None, ge=0.0)
    first_visible_section_ms: float | None = Field(default=None, ge=0.0)
    development_ready_ms: float | None = Field(default=None, ge=0.0)
    landscape_ready_ms: float | None = Field(default=None, ge=0.0)
    learning_path_ready_ms: float | None = Field(default=None, ge=0.0)
    profile_ms: float = Field(default=0.0, ge=0.0)
    planning_ms: float = Field(default=0.0, ge=0.0)
    stage_planning_ms: float = Field(default=0.0, ge=0.0)
    retrieval_ms: float = Field(default=0.0, ge=0.0)
    ranking_ms: float = Field(default=0.0, ge=0.0)
    generation_ms: float = Field(default=0.0, ge=0.0)
    evaluation_ms: float = Field(default=0.0, ge=0.0)
    repair_ms: float = Field(default=0.0, ge=0.0)
    model_calls: int = Field(default=0, ge=0)
    repair_model_calls: int = Field(default=0, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    usage_complete: bool = False
    selected_paper_count: int = Field(default=0, ge=0)
    valid_paper_count: int = Field(default=0, ge=0)
    quality_score: float | None = None
    quality_threshold: float | None = None
    hard_gate_passed: bool | None = None
    quality_state: str | None = None
    repair_triggered: bool = False
    retry_status: str = "not_needed"


class ModelBenchmarkSummary(OnlineModel):
    mode: BenchmarkMode
    model: str
    runs: int = Field(ge=0)
    generation_success_rate: float = Field(ge=0.0, le=1.0)
    delivery_success_rate: float = Field(ge=0.0, le=1.0)
    timeout_count: int = Field(ge=0)
    generation_failed_count: int = Field(ge=0)
    average_duration_ms: float = Field(ge=0.0)
    p50_duration_ms: float = Field(ge=0.0)
    p95_duration_ms: float = Field(ge=0.0)
    average_first_llm_delta_ms: float | None = Field(default=None, ge=0.0)
    p50_first_llm_delta_ms: float | None = Field(default=None, ge=0.0)
    p95_first_llm_delta_ms: float | None = Field(default=None, ge=0.0)
    average_first_visible_section_ms: float | None = Field(default=None, ge=0.0)
    average_generation_ms: float = Field(ge=0.0)
    average_model_calls: float = Field(ge=0.0)
    average_prompt_tokens: float = Field(ge=0.0)
    average_completion_tokens: float = Field(ge=0.0)
    average_total_tokens: float = Field(ge=0.0)
    total_tokens: int = Field(ge=0)
    usage_complete_rate: float = Field(ge=0.0, le=1.0)
    average_quality_score: float | None = None
    hard_gate_pass_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    repair_trigger_rate: float = Field(ge=0.0, le=1.0)


def benchmark_run_key(
    mode: BenchmarkMode,
    model: str,
    case_id: str,
    repeat: int,
) -> str:
    return f"{mode}:{model}:{case_id}:r{repeat}"


def build_schedule(
    *,
    modes: list[BenchmarkMode],
    models: list[str],
    cases: list[OnlineEvaluationCase],
    repeats: int,
    seed: int,
) -> list[tuple[BenchmarkMode, str, OnlineEvaluationCase, int]]:
    rng = random.Random(seed)
    schedule: list[tuple[BenchmarkMode, str, OnlineEvaluationCase, int]] = []
    for mode in modes:
        for repeat in range(1, repeats + 1):
            for case in cases:
                ordered_models = list(models)
                rng.shuffle(ordered_models)
                schedule.extend(
                    (mode, model, case, repeat) for model in ordered_models
                )
    return schedule


def run_benchmark_case(
    pipeline: Any,
    *,
    mode: BenchmarkMode,
    model: str,
    case: OnlineEvaluationCase,
    repeat: int,
) -> ModelBenchmarkRun:
    trace = DomainOnboardingRequestTrace()
    started = perf_counter()
    milestones: dict[str, float] = {}

    def progress(
        event: str,
        _progress: float,
        _provisional: bool,
        _replace_paths: list[str],
        _data: dict[str, Any],
    ) -> None:
        milestones.setdefault(event, round((perf_counter() - started) * 1000, 3))

    try:
        result = pipeline.run(
            DomainOnboardingRequest(query=case.query),
            trace,
            progress_callback=progress,
        )
        status = result.status
        error = result.error
    except Exception as exception:  # benchmark must preserve the remaining matrix
        result = None
        status = "runner_error"
        error = f"{type(exception).__name__}: {exception}"
    duration_ms = round((perf_counter() - started) * 1000, 3)
    trace.total_duration_ms = duration_ms
    quality = result.quality if result is not None else None
    output = result.output if result is not None else None
    repair_record = result.repair_record if result is not None else None
    first_visible = [
        milestones[event]
        for event in VISIBLE_SECTION_EVENTS
        if event in milestones
    ]
    total_calls = trace.first_model_calls + trace.retry_model_calls
    unreported = (
        trace.first_unreported_usage_calls + trace.retry_unreported_usage_calls
    )
    papers = output.papers if output is not None else []
    return ModelBenchmarkRun(
        run_key=benchmark_run_key(mode, model, case.case_id, repeat),
        mode=mode,
        model=model,
        case_id=case.case_id,
        domain=case.domain,
        repeat=repeat,
        status=status,
        generation_succeeded=output is not None,
        delivery_succeeded=status in {"ok", "quality_warning"},
        error=error,
        duration_ms=duration_ms,
        first_llm_delta_ms=milestones.get("llm_delta"),
        first_visible_section_ms=min(first_visible) if first_visible else None,
        development_ready_ms=milestones.get("development_ready"),
        landscape_ready_ms=milestones.get("landscape_ready"),
        learning_path_ready_ms=milestones.get("learning_path_ready"),
        profile_ms=trace.profile_duration_ms,
        planning_ms=trace.planning_duration_ms,
        stage_planning_ms=trace.stage_planning_duration_ms,
        retrieval_ms=trace.retrieval_duration_ms,
        ranking_ms=trace.ranking_duration_ms,
        generation_ms=trace.generation_duration_ms,
        evaluation_ms=trace.evaluation_duration_ms,
        repair_ms=trace.repair_duration_ms,
        model_calls=total_calls,
        repair_model_calls=trace.retry_model_calls,
        prompt_tokens=trace.first_usage.prompt_tokens + trace.retry_usage.prompt_tokens,
        completion_tokens=(
            trace.first_usage.completion_tokens + trace.retry_usage.completion_tokens
        ),
        total_tokens=trace.first_usage.total_tokens + trace.retry_usage.total_tokens,
        usage_complete=total_calls > 0 and unreported == 0,
        selected_paper_count=len(papers),
        valid_paper_count=sum(
            bool(paper.paper_id and paper.title and paper.year and paper.url)
            for paper in papers
        ),
        quality_score=quality.score if quality is not None else None,
        quality_threshold=quality.threshold if quality is not None else None,
        hard_gate_passed=(
            quality.passed_hard_gates if quality is not None else None
        ),
        quality_state=quality.state if quality is not None else None,
        repair_triggered=bool(repair_record and repair_record.triggered),
        retry_status=quality.retry_status if quality is not None else "not_needed",
    )


def summarize_runs(runs: list[ModelBenchmarkRun]) -> list[ModelBenchmarkSummary]:
    grouped: dict[tuple[BenchmarkMode, str], list[ModelBenchmarkRun]] = defaultdict(list)
    for run in runs:
        grouped[(run.mode, run.model)].append(run)
    summaries: list[ModelBenchmarkSummary] = []
    for (mode, model), items in sorted(grouped.items()):
        durations = [item.duration_ms for item in items]
        delta_times = [
            item.first_llm_delta_ms
            for item in items
            if item.first_llm_delta_ms is not None
        ]
        visible_times = [
            item.first_visible_section_ms
            for item in items
            if item.first_visible_section_ms is not None
        ]
        qualities = [
            item.quality_score for item in items if item.quality_score is not None
        ]
        gates = [
            item.hard_gate_passed
            for item in items
            if item.hard_gate_passed is not None
        ]
        count = len(items)
        summaries.append(
            ModelBenchmarkSummary(
                mode=mode,
                model=model,
                runs=count,
                generation_success_rate=_ratio(
                    sum(item.generation_succeeded for item in items), count
                ),
                delivery_success_rate=_ratio(
                    sum(item.delivery_succeeded for item in items), count
                ),
                timeout_count=sum(item.status == "timeout" for item in items),
                generation_failed_count=sum(
                    item.status == "generation_failed" for item in items
                ),
                average_duration_ms=_average(durations),
                p50_duration_ms=_percentile(durations, 0.50),
                p95_duration_ms=_percentile(durations, 0.95),
                average_first_llm_delta_ms=_optional_average(delta_times),
                p50_first_llm_delta_ms=_optional_percentile(delta_times, 0.50),
                p95_first_llm_delta_ms=_optional_percentile(delta_times, 0.95),
                average_first_visible_section_ms=_optional_average(visible_times),
                average_generation_ms=_average(
                    [item.generation_ms for item in items]
                ),
                average_model_calls=_average(
                    [float(item.model_calls) for item in items]
                ),
                average_prompt_tokens=_average(
                    [float(item.prompt_tokens) for item in items]
                ),
                average_completion_tokens=_average(
                    [float(item.completion_tokens) for item in items]
                ),
                average_total_tokens=_average(
                    [float(item.total_tokens) for item in items]
                ),
                total_tokens=sum(item.total_tokens for item in items),
                usage_complete_rate=_ratio(
                    sum(item.usage_complete for item in items), count
                ),
                average_quality_score=_optional_average(qualities),
                hard_gate_pass_rate=(
                    _ratio(sum(bool(value) for value in gates), len(gates))
                    if gates
                    else None
                ),
                repair_trigger_rate=_ratio(
                    sum(item.repair_triggered for item in items), count
                ),
            )
        )
    return summaries


def load_completed_runs(path: str | Path) -> list[ModelBenchmarkRun]:
    source = Path(path)
    if not source.exists():
        return []
    by_key: dict[str, ModelBenchmarkRun] = {}
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        run = ModelBenchmarkRun.model_validate_json(line)
        by_key[run.run_key] = run
    return list(by_key.values())


def is_resumable_complete(run: ModelBenchmarkRun) -> bool:
    """Successful generations require reported usage; failures remain evidence."""
    return (
        not is_transient_infrastructure_failure(run)
        and (not run.generation_succeeded or run.usage_complete)
    )


def is_transient_infrastructure_failure(run: ModelBenchmarkRun) -> bool:
    """Identify retryable connection and provider-throttling failures."""
    error = (run.error or "").casefold()
    if not run.generation_succeeded and any(
        marker in error
        for marker in ("rate limit exceeded", "throttling_error", "code: 429")
    ):
        return True
    if (
        run.status == "timeout"
        and not run.generation_succeeded
        and run.generation_ms == 0
        and "deadline exceeded during ranking" in error
    ):
        return True
    return (
        not run.generation_succeeded
        and run.total_tokens == 0
        and run.first_llm_delta_ms is None
        and run.duration_ms < 10_000
        and any(
            marker in error
            for marker in (
                "connection error",
                "connection refused",
                "name resolution",
                "temporarily unavailable",
            )
        )
    )


def append_run(path: str | Path, run: ModelBenchmarkRun) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as stream:
        stream.write(run.model_dump_json() + "\n")
        stream.flush()


def write_benchmark_outputs(
    output_dir: str | Path,
    runs: list[ModelBenchmarkRun],
) -> tuple[Path, Path, Path]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    details_path = directory / "model-benchmark-runs.csv"
    summary_path = directory / "model-benchmark-summary.csv"
    report_path = directory / "model-benchmark-report.json"
    summaries = summarize_runs(runs)
    _write_models_csv(details_path, runs)
    _write_models_csv(summary_path, summaries)
    report_path.write_text(
        json.dumps(
            {
                "run_count": len(runs),
                "runs": [run.model_dump(mode="json") for run in runs],
                "summaries": [item.model_dump(mode="json") for item in summaries],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return details_path, summary_path, report_path


class FrozenPlanner:
    model = None

    def __init__(self, plan: DomainResearchPlan) -> None:
        self._plan = plan

    def plan(self, *_args: Any, **_kwargs: Any) -> PlanningResult:
        return PlanningResult(
            plan=self._plan.model_copy(deep=True),
            stats=ModelCallStats(),
        )


class FrozenRetriever:
    def __init__(self, papers: list[RankedPaper]) -> None:
        self.papers = papers

    def search(self, *_args: Any, **_kwargs: Any) -> RetrievalResult:
        return RetrievalResult(
            papers=[paper.model_copy(deep=True) for paper in self.papers],
            stats=RetrievalStats(),
        )

    def close(self) -> None:
        return None


class FrozenRanker:
    def __init__(self, papers: list[RankedPaper]) -> None:
        self.papers = papers

    def rank(self, *_args: Any, limit: int, **_kwargs: Any) -> RankingResult:
        selected = [paper.model_copy(deep=True) for paper in self.papers[:limit]]
        roles = sorted({paper.paper_role for paper in selected})
        return RankingResult(
            papers=selected,
            stats=RankingStats(
                deduplicated_count=len(self.papers),
                covered_roles=roles,
                selected_role_counts={
                    role: sum(paper.paper_role == role for paper in selected)
                    for role in roles
                },
                ranking_strategy="frozen_benchmark_input",
            ),
        )


class FrozenCoverageAnalyzer:
    def analyze(
        self,
        plan: DomainResearchPlan,
        papers: list[RankedPaper],
    ) -> CoverageAnalysis:
        return CoverageAnalysis(
            gaps=[],
            covered_subdirections={
                subdirection: [paper.paper_id for paper in papers]
                for subdirection in plan.expected_subdirections
            },
            covered_roles=sorted({paper.paper_role for paper in papers}),
        )


def _write_models_csv(path: Path, items: list[OnlineModel]) -> None:
    rows = [item.model_dump(mode="json") for item in items]
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator if denominator else 0.0, 6)


def _average(values: list[float]) -> float:
    return round(fmean(values), 3) if values else 0.0


def _optional_average(values: list[float]) -> float | None:
    return _average(values) if values else None


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if percentile == 0.50:
        return round(float(median(ordered)), 3)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * percentile + 0.999999)))
    return round(float(ordered[index]), 3)


def _optional_percentile(
    values: list[float],
    percentile: float,
) -> float | None:
    return _percentile(values, percentile) if values else None
