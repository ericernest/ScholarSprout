"""聚合 V0 兼容指标与 V1 分阶段、论文和分层质量指标。"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from math import ceil
from statistics import fmean
from threading import Lock
from typing import Any

from runtime.agent_runner import TokenUsage


@dataclass(slots=True)
class DomainOnboardingRequestTrace:
    status: str = "unknown"
    total_duration_ms: float = 0.0
    first_call_duration_ms: float = 0.0
    retry_call_duration_ms: float = 0.0
    first_model_calls: int = 0
    retry_model_calls: int = 0
    first_unreported_usage_calls: int = 0
    retry_unreported_usage_calls: int = 0
    first_usage: TokenUsage = field(default_factory=TokenUsage)
    retry_usage: TokenUsage = field(default_factory=TokenUsage)
    first_score: int | float | None = None
    final_score: int | float | None = None
    retry_status: str = "not_needed"

    profile_duration_ms: float = 0.0
    planning_duration_ms: float = 0.0
    retrieval_duration_ms: float = 0.0
    ranking_duration_ms: float = 0.0
    generation_duration_ms: float = 0.0
    evaluation_duration_ms: float = 0.0
    repair_duration_ms: float = 0.0

    search_query_count: int = 0
    retrieved_paper_count: int = 0
    deduplicated_paper_count: int = 0
    verified_paper_count: int = 0
    selected_paper_count: int = 0
    invalid_paper_count: int = 0

    first_dimensions: dict[str, float] = field(default_factory=dict)
    final_dimensions: dict[str, float] = field(default_factory=dict)
    quality_delta: float = 0.0
    repair_reason: str = "not_needed"
    retrieval_error_count: int = 0
    retrieval_retry_count: int = 0
    retrieval_cache_hit_count: int = 0
    retrieval_request_count: int = 0
    retrieval_source_success_count: int = 0
    retrieval_source_failure_count: int = 0

    @property
    def retry_attempted(self) -> bool:
        return self.retry_model_calls > 0 or self.repair_reason not in {"", "not_needed"}


def _duration_summary(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0, "average_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0}
    ordered = sorted(values)

    def percentile(percent: float) -> float:
        return round(ordered[max(0, ceil(percent * len(ordered)) - 1)], 3)

    return {
        "count": len(values),
        "average_ms": round(fmean(values), 3),
        "p50_ms": percentile(0.50),
        "p95_ms": percentile(0.95),
    }


class DomainOnboardingMetrics:
    stage_names = (
        "profile", "planning", "retrieval", "ranking", "generation", "evaluation", "repair"
    )

    def __init__(
        self,
        *,
        window_size: int = 200,
        input_cost_per_million_tokens: float | None = None,
        output_cost_per_million_tokens: float | None = None,
    ) -> None:
        self._lock = Lock()
        self._window_size = window_size
        self._input_cost = input_cost_per_million_tokens
        self._output_cost = output_cost_per_million_tokens
        self._requests_total = 0
        self._statuses: Counter[str] = Counter()
        self._retry_requests = 0
        self._retry_improved = 0
        self._repair_reasons: Counter[str] = Counter()
        self._extra_model_calls = 0
        self._extra_usage = TokenUsage()
        self._primary_model_calls = 0
        self._primary_unreported_usage_calls = 0
        self._retry_unreported_usage_calls = 0
        self._primary_usage = TokenUsage()
        self._request_durations: deque[float] = deque(maxlen=window_size)
        self._first_call_durations: deque[float] = deque(maxlen=window_size)
        self._retry_call_durations: deque[float] = deque(maxlen=window_size)
        self._stage_durations: dict[str, deque[float]] = {
            stage: deque(maxlen=window_size) for stage in self.stage_names
        }
        self._paper_totals: Counter[str] = Counter()
        self._first_dimension_values: dict[str, list[float]] = defaultdict(list)
        self._final_dimension_values: dict[str, list[float]] = defaultdict(list)
        self._quality_deltas: deque[float] = deque(maxlen=window_size)

    def record(self, trace: DomainOnboardingRequestTrace) -> None:
        with self._lock:
            self._requests_total += 1
            self._statuses[trace.status] += 1
            self._request_durations.append(trace.total_duration_ms)
            self._primary_model_calls += trace.first_model_calls
            self._primary_unreported_usage_calls += trace.first_unreported_usage_calls
            self._primary_usage.add(trace.first_usage)
            if trace.first_model_calls:
                self._first_call_durations.append(trace.first_call_duration_ms)
            if trace.retry_attempted:
                self._retry_requests += 1
                self._retry_call_durations.append(trace.retry_call_duration_ms or trace.repair_duration_ms)
                self._extra_model_calls += trace.retry_model_calls
                self._retry_unreported_usage_calls += trace.retry_unreported_usage_calls
                self._extra_usage.add(trace.retry_usage)
                self._repair_reasons[trace.repair_reason] += 1
                if trace.retry_status == "improved":
                    self._retry_improved += 1
            for stage in self.stage_names:
                value = float(getattr(trace, f"{stage}_duration_ms"))
                if value > 0:
                    self._stage_durations[stage].append(value)
            for field_name in (
                "search_query_count", "retrieved_paper_count", "deduplicated_paper_count",
                "verified_paper_count", "selected_paper_count", "invalid_paper_count",
                "retrieval_error_count", "retrieval_retry_count", "retrieval_cache_hit_count",
                "retrieval_request_count", "retrieval_source_success_count",
                "retrieval_source_failure_count",
            ):
                self._paper_totals[field_name] += int(getattr(trace, field_name))
            for name, value in trace.first_dimensions.items():
                self._first_dimension_values[name].append(value)
            for name, value in trace.final_dimensions.items():
                self._final_dimension_values[name].append(value)
            self._quality_deltas.append(trace.quality_delta)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            requests_total = self._requests_total
            retry_requests = self._retry_requests
            extra_prompt_tokens = self._extra_usage.prompt_tokens
            extra_completion_tokens = self._extra_usage.completion_tokens
            pricing_configured = self._input_cost is not None and self._output_cost is not None
            estimated_cost = None
            retry_usage_complete = (
                self._extra_model_calls > 0 and self._retry_unreported_usage_calls == 0
            )
            if pricing_configured and retry_usage_complete:
                estimated_cost = round(
                    extra_prompt_tokens / 1_000_000 * self._input_cost
                    + extra_completion_tokens / 1_000_000 * self._output_cost,
                    8,
                )
            return {
                "requests_total": requests_total,
                "statuses": dict(self._statuses),
                "retry": {
                    "requests": retry_requests,
                    "rate": round(retry_requests / requests_total if requests_total else 0.0, 4),
                    "improved": self._retry_improved,
                    "improvement_rate": round(
                        self._retry_improved / retry_requests if retry_requests else 0.0, 4
                    ),
                    "reasons": dict(self._repair_reasons),
                },
                "latency": {
                    "request": _duration_summary(list(self._request_durations)),
                    "first_call": _duration_summary(list(self._first_call_durations)),
                    "retry_call": _duration_summary(list(self._retry_call_durations)),
                    "window_size": self._window_size,
                },
                "stage_latency": {
                    stage: _duration_summary(list(values))
                    for stage, values in self._stage_durations.items()
                },
                "papers": dict(self._paper_totals),
                "quality": {
                    "first_dimensions": self._dimension_averages(self._first_dimension_values),
                    "final_dimensions": self._dimension_averages(self._final_dimension_values),
                    "average_delta": round(fmean(self._quality_deltas), 6) if self._quality_deltas else 0.0,
                },
                "extra_call_cost": {
                    "model_calls": self._extra_model_calls,
                    "prompt_tokens": extra_prompt_tokens,
                    "completion_tokens": extra_completion_tokens,
                    "total_tokens": self._extra_usage.total_tokens,
                    "usage_reported": self._extra_usage.reported,
                    "usage_complete": retry_usage_complete,
                    "unreported_usage_calls": self._retry_unreported_usage_calls,
                    "pricing_configured": pricing_configured,
                    "estimated_cost": estimated_cost,
                },
                "model_usage": self._model_usage_snapshot(),
            }

    def _model_usage_snapshot(self) -> dict[str, Any]:
        total_usage = TokenUsage()
        total_usage.add(self._primary_usage)
        total_usage.add(self._extra_usage)
        total_calls = self._primary_model_calls + self._extra_model_calls
        total_unreported = (
            self._primary_unreported_usage_calls + self._retry_unreported_usage_calls
        )
        return {
            "primary": self._usage_snapshot(
                self._primary_model_calls,
                self._primary_usage,
                self._primary_unreported_usage_calls,
            ),
            "retry": self._usage_snapshot(
                self._extra_model_calls,
                self._extra_usage,
                self._retry_unreported_usage_calls,
            ),
            "total": self._usage_snapshot(total_calls, total_usage, total_unreported),
        }

    @staticmethod
    def _usage_snapshot(
        model_calls: int,
        usage: TokenUsage,
        unreported_usage_calls: int,
    ) -> dict[str, int | bool]:
        return {
            "model_calls": model_calls,
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
            "usage_reported": usage.reported,
            "usage_complete": model_calls > 0 and unreported_usage_calls == 0,
            "unreported_usage_calls": unreported_usage_calls,
        }

    @staticmethod
    def _dimension_averages(values: dict[str, list[float]]) -> dict[str, float]:
        return {name: round(fmean(items), 6) for name, items in values.items() if items}
