"""聚合领域入门调用耗时、重试效果和额外 token 成本。"""

from __future__ import annotations

from collections import Counter, deque
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
    first_usage: TokenUsage = field(default_factory=TokenUsage)
    retry_usage: TokenUsage = field(default_factory=TokenUsage)
    first_score: int | None = None
    final_score: int | None = None
    retry_status: str = "not_needed"

    @property
    def retry_attempted(self) -> bool:
        return self.retry_model_calls > 0


def _duration_summary(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {
            "count": 0,
            "average_ms": 0.0,
            "p50_ms": 0.0,
            "p95_ms": 0.0,
        }

    ordered = sorted(values)

    def percentile(percent: float) -> float:
        index = max(0, ceil(percent * len(ordered)) - 1)
        return round(ordered[index], 3)

    return {
        "count": len(values),
        "average_ms": round(fmean(values), 3),
        "p50_ms": percentile(0.50),
        "p95_ms": percentile(0.95),
    }


class DomainOnboardingMetrics:
    def __init__(
        self,
        *,
        window_size: int = 200,
        input_cost_per_million_tokens: float | None = None,
        output_cost_per_million_tokens: float | None = None,
    ):
        self._lock = Lock()
        self._window_size = window_size
        self._input_cost = input_cost_per_million_tokens
        self._output_cost = output_cost_per_million_tokens
        self._requests_total = 0
        self._statuses: Counter[str] = Counter()
        self._retry_requests = 0
        self._retry_improved = 0
        self._extra_model_calls = 0
        self._extra_usage = TokenUsage()
        self._request_durations: deque[float] = deque(maxlen=window_size)
        self._first_call_durations: deque[float] = deque(maxlen=window_size)
        self._retry_call_durations: deque[float] = deque(maxlen=window_size)

    def record(self, trace: DomainOnboardingRequestTrace) -> None:
        with self._lock:
            self._requests_total += 1
            self._statuses[trace.status] += 1
            self._request_durations.append(trace.total_duration_ms)
            if trace.first_model_calls:
                self._first_call_durations.append(trace.first_call_duration_ms)
            if trace.retry_attempted:
                self._retry_requests += 1
                self._retry_call_durations.append(trace.retry_call_duration_ms)
                self._extra_model_calls += trace.retry_model_calls
                self._extra_usage.add(trace.retry_usage)
                if trace.retry_status == "improved":
                    self._retry_improved += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            requests_total = self._requests_total
            retry_requests = self._retry_requests
            retry_improved = self._retry_improved
            extra_prompt_tokens = self._extra_usage.prompt_tokens
            extra_completion_tokens = self._extra_usage.completion_tokens
            pricing_configured = (
                self._input_cost is not None and self._output_cost is not None
            )
            estimated_cost = None
            if pricing_configured and self._extra_usage.reported:
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
                    "rate": round(
                        retry_requests / requests_total if requests_total else 0.0,
                        4,
                    ),
                    "improved": retry_improved,
                    "improvement_rate": round(
                        retry_improved / retry_requests if retry_requests else 0.0,
                        4,
                    ),
                },
                "latency": {
                    "request": _duration_summary(list(self._request_durations)),
                    "first_call": _duration_summary(list(self._first_call_durations)),
                    "retry_call": _duration_summary(list(self._retry_call_durations)),
                    "window_size": self._window_size,
                },
                "extra_call_cost": {
                    "model_calls": self._extra_model_calls,
                    "prompt_tokens": extra_prompt_tokens,
                    "completion_tokens": extra_completion_tokens,
                    "total_tokens": self._extra_usage.total_tokens,
                    "usage_reported": self._extra_usage.reported,
                    "pricing_configured": pricing_configured,
                    "estimated_cost": estimated_cost,
                },
            }
