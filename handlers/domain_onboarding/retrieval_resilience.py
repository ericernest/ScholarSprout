"""论文检索的重试、限速和 TTL 查询缓存。"""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from threading import Lock
from time import monotonic, sleep
from typing import Any, Callable, Generic, TypeVar

import httpx

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class RetrievalRetryPolicy:
    max_attempts: int = 3
    base_backoff_seconds: float = 0.5
    max_backoff_seconds: float = 8.0
    retry_status_codes: frozenset[int] = field(
        default_factory=lambda: frozenset({408, 425, 429, 500, 502, 503, 504})
    )

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if self.base_backoff_seconds < 0 or self.max_backoff_seconds < self.base_backoff_seconds:
            raise ValueError("invalid retrieval backoff settings")


class TTLQueryCache(Generic[T]):
    """有界、线程安全的进程内 TTL 缓存；读写均返回深拷贝。"""

    def __init__(
        self,
        *,
        ttl_seconds: float,
        max_entries: int,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._clock = clock
        self._lock = Lock()
        self._values: OrderedDict[str, tuple[float, T]] = OrderedDict()

    def get(self, key: str) -> T | None:
        if self.ttl_seconds <= 0 or self.max_entries <= 0:
            return None
        with self._lock:
            item = self._values.get(key)
            if item is None:
                return None
            expires_at, value = item
            if expires_at <= self._clock():
                return None
            self._values.move_to_end(key)
            return deepcopy(value)

    def get_stale(self, key: str, *, max_stale_seconds: float) -> T | None:
        if max_stale_seconds <= 0 or self.max_entries <= 0:
            return None
        with self._lock:
            item = self._values.get(key)
            if item is None:
                return None
            expires_at, value = item
            if expires_at + max_stale_seconds <= self._clock():
                self._values.pop(key, None)
                return None
            self._values.move_to_end(key)
            return deepcopy(value)

    def set(self, key: str, value: T) -> None:
        if self.ttl_seconds <= 0 or self.max_entries <= 0:
            return
        with self._lock:
            self._values[key] = (self._clock() + self.ttl_seconds, deepcopy(value))
            self._values.move_to_end(key)
            while len(self._values) > self.max_entries:
                self._values.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._values.clear()


class ResilientHTTPClient:
    """对可恢复 HTTP 故障进行有上限的退避重试，并支持最小请求间隔。"""

    def __init__(
        self,
        client: httpx.Client,
        *,
        retry_policy: RetrievalRetryPolicy,
        min_interval_seconds: float = 0.0,
        sleep_func: Callable[[float], None] = sleep,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.client = client
        self.retry_policy = retry_policy
        self.min_interval_seconds = min_interval_seconds
        self._sleep = sleep_func
        self._clock = clock
        self._rate_lock = Lock()
        self._next_request_at = 0.0
        self.retry_count = 0
        self.request_count = 0
        self.rate_limit_count = 0

    def reset_stats(self) -> None:
        self.retry_count = 0
        self.request_count = 0
        self.rate_limit_count = 0

    def get(self, url: str, *, params: dict[str, Any]) -> Any:
        last_error: httpx.HTTPError | None = None
        for attempt in range(1, self.retry_policy.max_attempts + 1):
            self._wait_for_slot()
            self.request_count += 1
            try:
                response = self.client.get(url, params=params)
            except httpx.TransportError as error:
                last_error = error
                if attempt >= self.retry_policy.max_attempts:
                    raise
                self._retry_after(None, attempt)
                continue

            status_code = getattr(response, "status_code", None)
            if status_code is None:
                response.raise_for_status()
                return response
            if status_code == 429:
                self.rate_limit_count += 1
            if status_code in self.retry_policy.retry_status_codes:
                if attempt >= self.retry_policy.max_attempts:
                    response.raise_for_status()
                self._retry_after(response, attempt)
                continue
            response.raise_for_status()
            return response

        if last_error is not None:
            raise last_error
        raise RuntimeError("retrieval retry loop ended without a response")

    def _wait_for_slot(self) -> None:
        if self.min_interval_seconds <= 0:
            return
        with self._rate_lock:
            delay = max(0.0, self._next_request_at - self._clock())
            if delay > 0:
                self._sleep(delay)
            self._next_request_at = self._clock() + self.min_interval_seconds

    def _retry_after(self, response: httpx.Response | None, attempt: int) -> None:
        self.retry_count += 1
        delay = self.retry_policy.base_backoff_seconds * (2 ** (attempt - 1))
        if response is not None:
            delay = self._parse_retry_after(response.headers.get("Retry-After")) or delay
        self._sleep(min(delay, self.retry_policy.max_backoff_seconds))

    @staticmethod
    def _parse_retry_after(value: str | None) -> float | None:
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(value)
            except (TypeError, ValueError, OverflowError):
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())


class ProviderCircuitBreaker:
    """线程安全的连续失败熔断器；冷却后允许一次探测请求。"""

    def __init__(
        self,
        *,
        failure_threshold: int,
        cooldown_seconds: float,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be positive")
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must not be negative")
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._lock = Lock()
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._probe_in_flight = False

    def allow_request(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return True
            if self._clock() - self._opened_at < self.cooldown_seconds:
                return False
            if self._probe_in_flight:
                return False
            self._probe_in_flight = True
            return True

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._opened_at = None
            self._probe_in_flight = False

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            self._probe_in_flight = False
            if self._consecutive_failures >= self.failure_threshold:
                self._opened_at = self._clock()

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._opened_at is not None
