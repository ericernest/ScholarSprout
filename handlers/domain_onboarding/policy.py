"""Versioned, immutable quality and repair policy contracts."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .schemas import QualityDimension, QualityIssueType


CURRENT_POLICY_VERSION = "domain-quality-v1.1.0"


def default_dimension_weights() -> dict[QualityDimension, float]:
    return {
        "structure": 0.15,
        "paper_validity": 0.15,
        "paper_relevance": 0.15,
        "evidence_grounding": 0.14,
        "topic_coverage": 0.13,
        "development_coherence": 0.11,
        "learning_path": 0.10,
        "goal_alignment": 0.07,
    }


def default_hard_gate_dimensions() -> dict[str, list[QualityDimension]]:
    return {
        "required_structure": ["structure"],
        "paper_identity": ["paper_validity"],
        "paper_relevance": ["paper_relevance"],
        "evidence_support": ["evidence_grounding"],
    }


def default_llm_repair_issue_types() -> list[QualityIssueType]:
    return [
        "missing_coverage",
        "weak_development_stage",
        "beginner_mismatch",
        "structure_error",
        "missing_evidence",
        "unsupported_claim",
        "low_paper_relevance",
    ]


def default_critical_dimensions() -> list[QualityDimension]:
    return [
        "structure",
        "paper_validity",
        "paper_relevance",
        "evidence_grounding",
        "learning_path",
    ]


class DomainOnboardingPolicy(BaseModel):
    """A replayable snapshot of every setting that changes quality decisions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = Field(
        default=CURRENT_POLICY_VERSION,
        pattern=r"^domain-quality-v\d+\.\d+\.\d+$",
    )
    quality_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    min_improvement_delta: float = Field(default=0.05, ge=0.0, le=1.0)
    dimension_weights: dict[QualityDimension, float] = Field(
        default_factory=default_dimension_weights
    )
    hard_gate_dimensions: dict[str, list[QualityDimension]] = Field(
        default_factory=default_hard_gate_dimensions
    )
    hard_gate_severities: list[Literal["error", "critical"]] = Field(
        default_factory=lambda: ["error", "critical"]
    )
    llm_repair_issue_types: list[QualityIssueType] = Field(
        default_factory=default_llm_repair_issue_types
    )
    critical_dimensions: list[QualityDimension] = Field(
        default_factory=default_critical_dimensions
    )

    @model_validator(mode="after")
    def validate_policy(self) -> "DomainOnboardingPolicy":
        expected = set(default_dimension_weights())
        if set(self.dimension_weights) != expected:
            raise ValueError("dimension_weights must define every quality dimension exactly once")
        if abs(sum(self.dimension_weights.values()) - 1.0) > 1e-9:
            raise ValueError("quality dimension weights must sum to 1.0")
        if not self.hard_gate_dimensions:
            raise ValueError("at least one hard gate must be configured")
        if not self.hard_gate_severities:
            raise ValueError("at least one hard gate severity must be configured")
        if any(not name.strip() or not dimensions for name, dimensions in self.hard_gate_dimensions.items()):
            raise ValueError("hard gates require a name and at least one dimension")
        if len(set(self.hard_gate_severities)) != len(self.hard_gate_severities):
            raise ValueError("hard_gate_severities must not contain duplicates")
        if len(set(self.llm_repair_issue_types)) != len(self.llm_repair_issue_types):
            raise ValueError("llm_repair_issue_types must not contain duplicates")
        if len(set(self.critical_dimensions)) != len(self.critical_dimensions):
            raise ValueError("critical_dimensions must not contain duplicates")
        return self

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class PolicyRegistry:
    """Rejects ambiguous version reuse and resolves immutable policies by version."""

    def __init__(self, policies: list[DomainOnboardingPolicy] | None = None) -> None:
        self._policies: dict[str, DomainOnboardingPolicy] = {}
        for policy in policies or []:
            self.register(policy)

    def register(self, policy: DomainOnboardingPolicy) -> None:
        existing = self._policies.get(policy.policy_version)
        if existing is not None and existing.fingerprint != policy.fingerprint:
            raise ValueError(
                f"policy version {policy.policy_version!r} is already registered with different content"
            )
        self._policies[policy.policy_version] = policy

    def get(self, policy_version: str) -> DomainOnboardingPolicy:
        try:
            return self._policies[policy_version]
        except KeyError as error:
            raise KeyError(f"unknown domain onboarding policy: {policy_version}") from error

    def versions(self) -> list[str]:
        return sorted(self._policies)
