"""Configurable per-stage model routing with bounded timeout fallback."""

from __future__ import annotations

from threading import Lock
from time import perf_counter
from typing import Any


class RoutedChatModel:
    """Try configured model IDs inside one caller-owned timeout budget."""

    def __init__(
        self,
        delegate: Any,
        model_names: list[str],
        *,
        route_name: str,
        timeout_reserve_seconds: float = 2.0,
    ) -> None:
        normalized = list(
            dict.fromkeys(name.strip() for name in model_names if name.strip())
        )
        if not normalized:
            raise ValueError("model route must contain at least one model")
        self.delegate = delegate
        self.model_names = normalized
        self.route_name = route_name
        self.timeout_reserve_seconds = timeout_reserve_seconds
        self._lock = Lock()
        self._last_call: dict[str, Any] = {
            "route": route_name,
            "configured_models": normalized,
            "attempts": [],
            "selected_model": None,
        }

    @property
    def last_attempt_count(self) -> int:
        with self._lock:
            return max(1, len(self._last_call["attempts"]))

    def chat(self, **kwargs: Any) -> Any:
        requested_timeout = kwargs.pop("timeout", None)
        attempt_timeout = self._attempt_timeout(requested_timeout)
        attempts: list[dict[str, Any]] = []
        last_error: Exception | None = None
        for model_name in self.model_names:
            started = perf_counter()
            try:
                response = self.delegate.chat(
                    **kwargs,
                    timeout=attempt_timeout,
                    model_name=model_name,
                )
            except Exception as error:
                last_error = error
                attempts.append(
                    {
                        "model": model_name,
                        "status": "failed",
                        "duration_ms": round((perf_counter() - started) * 1000, 3),
                        "error_type": type(error).__name__,
                    }
                )
                continue
            attempts.append(
                {
                    "model": model_name,
                    "status": "selected",
                    "duration_ms": round((perf_counter() - started) * 1000, 3),
                }
            )
            self._record(attempts, model_name)
            return response
        self._record(attempts, None)
        raise RuntimeError(
            f"all models failed for route {self.route_name}: "
            f"{type(last_error).__name__ if last_error else 'unknown error'}"
        ) from last_error

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        return self.delegate.embed(texts, model=model)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "route": self._last_call["route"],
                "configured_models": list(self._last_call["configured_models"]),
                "attempts": [dict(item) for item in self._last_call["attempts"]],
                "selected_model": self._last_call["selected_model"],
            }

    def _attempt_timeout(self, requested_timeout: float | None) -> float | None:
        if requested_timeout is None:
            return None
        usable = max(1.0, requested_timeout - self.timeout_reserve_seconds)
        return max(1.0, usable / len(self.model_names))

    def _record(
        self, attempts: list[dict[str, Any]], selected_model: str | None
    ) -> None:
        with self._lock:
            self._last_call = {
                "route": self.route_name,
                "configured_models": list(self.model_names),
                "attempts": [dict(item) for item in attempts],
                "selected_model": selected_model,
            }


class ModelOverrideChatModel:
    """Bind one model ID without owning or closing the shared client."""

    def __init__(self, delegate: Any, model_name: str) -> None:
        self.delegate = delegate
        self.model_name = model_name

    def chat(self, **kwargs: Any) -> Any:
        return self.delegate.chat(**kwargs, model_name=self.model_name)

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        return self.delegate.embed(texts, model=model)


def run_with_model_route(
    model: Any,
    operation: Any,
    *,
    timeout_seconds: float | None,
) -> Any:
    """Retry transport, JSON, and caller validation errors on the next model."""
    if not isinstance(model, RoutedChatModel):
        return operation(model, timeout_seconds)
    attempt_timeout = model._attempt_timeout(timeout_seconds)
    attempts: list[dict[str, Any]] = []
    last_error: Exception | None = None
    for model_name in model.model_names:
        candidate = ModelOverrideChatModel(model.delegate, model_name)
        started = perf_counter()
        try:
            result = operation(candidate, attempt_timeout)
        except Exception as error:
            last_error = error
            attempts.append(
                {
                    "model": model_name,
                    "status": "failed",
                    "duration_ms": round((perf_counter() - started) * 1000, 3),
                    "error_type": type(error).__name__,
                }
            )
            continue
        attempts.append(
            {
                "model": model_name,
                "status": "selected",
                "duration_ms": round((perf_counter() - started) * 1000, 3),
            }
        )
        model._record(attempts, model_name)
        return result
    model._record(attempts, None)
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"model route {model.route_name} has no candidates")


def routed_model_from_env(
    delegate: Any,
    env_value: str | None,
    *,
    route_name: str,
) -> Any:
    names = [item.strip() for item in (env_value or "").split(",") if item.strip()]
    return (
        RoutedChatModel(delegate, names, route_name=route_name)
        if names
        else delegate
    )


def routing_snapshot(model: Any) -> dict[str, Any] | None:
    snapshot = getattr(model, "snapshot", None)
    return snapshot() if callable(snapshot) else None
