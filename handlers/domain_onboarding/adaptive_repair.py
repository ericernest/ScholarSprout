"""Derive conservative, shadow-only repair recommendations from audit history."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field

from .audit import DomainOnboardingAuditRecord
from .schemas import ContentQuality, QualityIssueType, RepairActionType


class AdaptiveRepairConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    min_samples: int = Field(default=20, ge=1)
    confidence_z: float = Field(default=1.96, gt=0.0)
    allowed_actions: list[RepairActionType] = Field(
        default_factory=lambda: ["code", "retrieval", "llm"]
    )


class AdaptiveActionStats(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    samples: int = Field(ge=0)
    successes: int = Field(ge=0)
    success_rate: float = Field(ge=0.0, le=1.0)
    wilson_lower_bound: float = Field(ge=0.0, le=1.0)
    average_score_delta: float
    average_latency_ms: float = Field(ge=0.0)
    average_total_tokens: float = Field(ge=0.0)


class AdaptiveRepairStrategy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    issue_type: QualityIssueType
    preferred_action: RepairActionType
    evidence: AdaptiveActionStats


class AdaptiveRepairPolicy(BaseModel):
    """A reviewable candidate policy; loading it never changes repair execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = Field(pattern=r"^domain-repair-adaptive-v\d+\.\d+\.\d+$")
    generated_at: datetime
    source_quality_policy_versions: list[str]
    min_samples: int = Field(ge=1)
    strategies: dict[QualityIssueType, AdaptiveRepairStrategy] = Field(default_factory=dict)
    attribution_limit: str = (
        "Request-level outcomes may be shared by multiple repair actions; recommendations "
        "must remain shadow-only until validated by a controlled experiment."
    )

    def save(self, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "AdaptiveRepairPolicy":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


class AdaptiveRepairPolicyBuilder:
    def __init__(self, config: AdaptiveRepairConfig | None = None) -> None:
        self.config = config or AdaptiveRepairConfig()

    def build(
        self,
        records: Iterable[DomainOnboardingAuditRecord],
        *,
        policy_version: str = "domain-repair-adaptive-v1.0.0",
        generated_at: datetime | None = None,
    ) -> AdaptiveRepairPolicy:
        samples: dict[tuple[QualityIssueType, RepairActionType], list[tuple[bool, float, float, int]]] = defaultdict(list)
        quality_versions: set[str] = set()
        for record in records:
            quality_versions.add(record.policy_version)
            if not record.quality_attempts or record.repair_record is None:
                continue
            issues = {
                str(issue.issue_id): issue.issue_type
                for issue in record.quality_attempts[0].quality.issues
            }
            decision = record.repair_record.decision
            successful = bool(decision and decision.decision == "repaired_selected")
            score_delta = float(decision.score_delta if decision else 0.0)
            latency = float(record.total_duration_ms)
            tokens = int(record.token_usage.get("total_tokens", 0) or 0)
            for action in record.repair_record.actions:
                if action.status not in {"applied", "failed"}:
                    continue
                for issue_type in {issues[item] for item in action.issue_ids if item in issues}:
                    samples[(issue_type, action.action_type)].append(
                        (successful and action.status == "applied", score_delta, latency, tokens)
                    )

        strategies: dict[QualityIssueType, AdaptiveRepairStrategy] = {}
        issue_types = {issue_type for issue_type, _ in samples}
        for issue_type in issue_types:
            candidates: list[tuple[RepairActionType, AdaptiveActionStats]] = []
            for action_type in self.config.allowed_actions:
                observations = samples.get((issue_type, action_type), [])
                if len(observations) < self.config.min_samples:
                    continue
                successes = sum(item[0] for item in observations)
                stats = AdaptiveActionStats(
                    samples=len(observations),
                    successes=successes,
                    success_rate=successes / len(observations),
                    wilson_lower_bound=_wilson_lower_bound(
                        successes, len(observations), self.config.confidence_z
                    ),
                    average_score_delta=fmean(item[1] for item in observations),
                    average_latency_ms=fmean(item[2] for item in observations),
                    average_total_tokens=fmean(item[3] for item in observations),
                )
                candidates.append((action_type, stats))
            if not candidates:
                continue
            preferred_action, evidence = max(
                candidates,
                key=lambda item: (
                    item[1].wilson_lower_bound,
                    item[1].average_score_delta,
                    -item[1].average_total_tokens,
                    -item[1].average_latency_ms,
                ),
            )
            strategies[issue_type] = AdaptiveRepairStrategy(
                issue_type=issue_type,
                preferred_action=preferred_action,
                evidence=evidence,
            )
        return AdaptiveRepairPolicy(
            policy_version=policy_version,
            generated_at=generated_at or datetime.now(UTC),
            source_quality_policy_versions=sorted(quality_versions),
            min_samples=self.config.min_samples,
            strategies=strategies,
        )


class AdaptiveRepairAdvisor:
    """Returns recommendations for observability; it never executes an action."""

    def __init__(self, policy: AdaptiveRepairPolicy) -> None:
        self.policy = policy

    def recommend(
        self, quality: ContentQuality
    ) -> dict[QualityIssueType, RepairActionType]:
        issue_types = {issue.issue_type for issue in quality.issues}
        return {
            issue_type: strategy.preferred_action
            for issue_type, strategy in self.policy.strategies.items()
            if issue_type in issue_types
        }


def load_audit_records(paths: Iterable[str | Path]) -> list[DomainOnboardingAuditRecord]:
    records: list[DomainOnboardingAuditRecord] = []
    for raw_path in paths:
        path = Path(raw_path)
        files = sorted(path.glob("domain-onboarding-*.jsonl")) if path.is_dir() else [path]
        for file in files:
            for line_number, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    records.append(DomainOnboardingAuditRecord.model_validate_json(line))
                except Exception as error:
                    raise ValueError(f"invalid audit record at {file}:{line_number}: {error}") from error
    return records


def load_advisor(path: str | Path | None) -> AdaptiveRepairAdvisor | None:
    if path is None or not str(path).strip():
        return None
    return AdaptiveRepairAdvisor(AdaptiveRepairPolicy.load(path))


def _wilson_lower_bound(successes: int, samples: int, z: float) -> float:
    if samples == 0:
        return 0.0
    rate = successes / samples
    denominator = 1.0 + z * z / samples
    centre = rate + z * z / (2.0 * samples)
    margin = z * math.sqrt((rate * (1.0 - rate) + z * z / (4.0 * samples)) / samples)
    return max(0.0, (centre - margin) / denominator)
