"""请求级 deadline 与协作式取消控制。"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event
from time import perf_counter
from typing import Any, Callable


class PipelineExecutionHalted(RuntimeError):
    def __init__(self, *, status: str, stage: str, message: str, duration_ms: float = 0.0):
        super().__init__(message)
        self.status = status
        self.stage = stage
        self.duration_ms = duration_ms


class PipelineDeadlineExceeded(PipelineExecutionHalted):
    def __init__(self, *, stage: str, duration_ms: float = 0.0):
        super().__init__(
            status="timeout",
            stage=stage,
            message=f"domain onboarding deadline exceeded during {stage}",
            duration_ms=duration_ms,
        )


class PipelineCancelled(PipelineExecutionHalted):
    def __init__(self, *, stage: str, duration_ms: float = 0.0):
        super().__init__(
            status="cancelled",
            stage=stage,
            message=f"domain onboarding request cancelled during {stage}",
            duration_ms=duration_ms,
        )


@dataclass(slots=True)
class PipelineExecutionContext:
    """在阶段边界检查总 deadline 和调用方提供的取消信号。

    阻塞中的第三方调用仍由其原生 timeout 负责终止；本对象保证超时或取消后
    Pipeline 不再进入下一个阶段，也不会触发额外检索或修复。
    """

    timeout_seconds: float
    cancel_event: Event = field(default_factory=Event)
    clock: Callable[[], float] = perf_counter
    started_at: float = field(init=False)
    stage_durations_ms: dict[str, float] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.started_at = self.clock()

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.timeout_seconds - (self.clock() - self.started_at))

    def cancel(self) -> None:
        self.cancel_event.set()

    def checkpoint(self, stage: str) -> None:
        if self.cancel_event.is_set():
            raise PipelineCancelled(stage=stage)
        if self.remaining_seconds <= 0:
            raise PipelineDeadlineExceeded(stage=stage)

    def call(
        self,
        stage: str,
        stage_timeout_seconds: float,
        function: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> tuple[Any, float]:
        self.checkpoint(stage)
        allowed_seconds = min(stage_timeout_seconds, self.remaining_seconds)
        started = self.clock()
        try:
            result = function(*args, **kwargs)
        except Exception as error:
            duration_ms = self._record_duration(stage, started)
            self._check_after_call(stage, started, allowed_seconds, duration_ms, cause=error)
            raise
        duration_ms = self._record_duration(stage, started)
        self._check_after_call(stage, started, allowed_seconds, duration_ms)
        return result, duration_ms

    def _record_duration(self, stage: str, started: float) -> float:
        duration_ms = round((self.clock() - started) * 1000, 3)
        self.stage_durations_ms[stage] = self.stage_durations_ms.get(stage, 0.0) + duration_ms
        return duration_ms

    def _check_after_call(
        self,
        stage: str,
        started: float,
        allowed_seconds: float,
        duration_ms: float,
        *,
        cause: Exception | None = None,
    ) -> None:
        if self.cancel_event.is_set():
            raise PipelineCancelled(stage=stage, duration_ms=duration_ms) from cause
        if self.clock() - started > allowed_seconds or self.remaining_seconds <= 0:
            raise PipelineDeadlineExceeded(stage=stage, duration_ms=duration_ms) from cause
