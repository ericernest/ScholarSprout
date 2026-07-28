"""Privacy-preserving, append-only request audit records."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .metrics import DomainOnboardingRequestTrace
from .schemas import PipelineResult, QualityAttempt, RepairRecord


class DomainOnboardingAuditRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audit_schema_version: str = "1.0"
    request_id: str
    recorded_at: datetime
    query_hash: str
    session_hash: str | None = None
    user_hash: str | None = None
    policy_version: str
    policy_fingerprint: str | None = None
    status: str
    total_duration_ms: float = Field(ge=0.0)
    stage_durations_ms: dict[str, float] = Field(default_factory=dict)
    model_calls: dict[str, int] = Field(default_factory=dict)
    token_usage: dict[str, int | bool] = Field(default_factory=dict)
    paper_counts: dict[str, int] = Field(default_factory=dict)
    selected_paper_ids: list[str] = Field(default_factory=list)
    quality_attempts: list[QualityAttempt] = Field(default_factory=list)
    repair_record: RepairRecord | None = None
    interrupted_stage: str | None = None
    deadline_exceeded: bool = False
    cancelled: bool = False
    knowledge_graph: dict[str, Any] = Field(default_factory=dict)


class AuditSink(Protocol):
    def write(self, record: DomainOnboardingAuditRecord) -> None: ...

    def close(self) -> None: ...


class NoOpAuditSink:
    def write(self, record: DomainOnboardingAuditRecord) -> None:
        return None

    def close(self) -> None:
        return None


class JsonlAuditSink:
    """Append one complete JSON object per request to a daily file."""

    def __init__(self, directory: str | Path, *, fsync: bool = False) -> None:
        self.directory = Path(directory).expanduser()
        self.fsync = fsync
        self._lock = Lock()

    def write(self, record: DomainOnboardingAuditRecord) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        day = record.recorded_at.astimezone(UTC).date().isoformat()
        path = self.directory / f"domain-onboarding-{day}.jsonl"
        payload = (
            json.dumps(
                record.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        with self._lock:
            descriptor = os.open(
                path,
                os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                0o600,
            )
            try:
                remaining = memoryview(payload)
                while remaining:
                    written = os.write(descriptor, remaining)
                    if written <= 0:
                        raise OSError("audit append made no progress")
                    remaining = remaining[written:]
                if self.fsync:
                    os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def close(self) -> None:
        return None


def create_audit_sink_from_env() -> AuditSink:
    directory = os.getenv("DOMAIN_ONBOARDING_AUDIT_DIR", "").strip()
    if not directory:
        return NoOpAuditSink()
    fsync = os.getenv("DOMAIN_ONBOARDING_AUDIT_FSYNC", "").strip().lower()
    return JsonlAuditSink(directory, fsync=fsync in {"1", "true", "yes", "on"})


def build_audit_record(
    trace: DomainOnboardingRequestTrace,
    *,
    query: str,
    session_id: str | None,
    user_id: str | None,
    result: PipelineResult | None,
) -> DomainOnboardingAuditRecord:
    return DomainOnboardingAuditRecord(
        request_id=trace.request_id,
        recorded_at=datetime.now(UTC),
        query_hash=_private_hash(query) or _private_hash("empty-query") or "",
        session_hash=_private_hash(session_id),
        user_hash=_private_hash(user_id),
        policy_version=trace.policy_version,
        policy_fingerprint=trace.policy_fingerprint,
        status=trace.status,
        total_duration_ms=trace.total_duration_ms,
        stage_durations_ms={
            stage: float(getattr(trace, f"{stage}_duration_ms"))
            for stage in _STAGES
            if float(getattr(trace, f"{stage}_duration_ms")) > 0
        },
        model_calls={
            "primary": trace.first_model_calls,
            "repair": trace.retry_model_calls,
            "total": trace.first_model_calls + trace.retry_model_calls,
        },
        token_usage={
            "prompt_tokens": trace.first_usage.prompt_tokens + trace.retry_usage.prompt_tokens,
            "completion_tokens": (
                trace.first_usage.completion_tokens + trace.retry_usage.completion_tokens
            ),
            "total_tokens": trace.first_usage.total_tokens + trace.retry_usage.total_tokens,
            "complete": (
                trace.first_unreported_usage_calls == 0
                and trace.retry_unreported_usage_calls == 0
            ),
        },
        paper_counts={name: int(getattr(trace, name)) for name in _PAPER_COUNT_FIELDS},
        selected_paper_ids=(
            [paper.paper_id for paper in result.output.papers]
            if result is not None and result.output is not None
            else []
        ),
        quality_attempts=list(result.quality_attempts) if result is not None else [],
        repair_record=(
            _redacted_repair_record(result.repair_record)
            if result is not None and result.repair_record is not None
            else None
        ),
        interrupted_stage=trace.interrupted_stage,
        deadline_exceeded=trace.deadline_exceeded,
        cancelled=trace.cancelled,
        knowledge_graph={
            "enabled": bool(result and result.knowledge_graph),
            "valid": trace.knowledge_graph_valid,
            "node_count": trace.knowledge_graph_node_count,
            "edge_count": trace.knowledge_graph_edge_count,
            "fallback_used": trace.knowledge_graph_fallback_used,
            "build_failed": trace.knowledge_graph_build_failed,
            "duration_ms": trace.knowledge_graph_duration_ms,
        },
    )


_STAGES = (
    "profile",
    "planning",
    "retrieval",
    "ranking",
    "generation",
    "evaluation",
    "repair",
)

_PAPER_COUNT_FIELDS = (
    "search_query_count",
    "retrieved_paper_count",
    "deduplicated_paper_count",
    "verified_paper_count",
    "selected_paper_count",
    "invalid_paper_count",
)


def _private_hash(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _redacted_repair_record(record: RepairRecord) -> RepairRecord:
    redacted = record.model_copy(deep=True)
    for action in redacted.actions:
        action.error = None
    return redacted
