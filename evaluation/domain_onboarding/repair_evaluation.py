"""Replayable repair-action and selection evaluation cases."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from handlers.domain_onboarding.repair_selection import RepairSelectionPolicy
from handlers.domain_onboarding.schemas import (
    ContentQuality,
    QualityDimension,
    QualityIssue,
    QualityIssueType,
    RepairActionRecord,
    RepairActionType,
    RepairRecord,
)


class RepairEvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RepairQualitySnapshot(RepairEvaluationModel):
    score: float = Field(ge=0.0, le=1.0)
    passed_hard_gates: bool
    dimensions: dict[QualityDimension, float]


class RepairEvaluationCase(RepairEvaluationModel):
    case_id: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    source: Literal["controlled_online", "production_audit"]
    issue_type: QualityIssueType
    target_path: str = Field(min_length=1)
    expected_action: RepairActionType
    first: RepairQualitySnapshot
    retry: RepairQualitySnapshot
    expected_selected_attempt: Literal[1, 2]
    annotation_status: Literal["seed", "human_verified"] = "seed"
    context: dict[str, object] = Field(default_factory=dict)


class RepairEvaluationReport(RepairEvaluationModel):
    dataset_version: str
    case_count: int
    annotation_status_counts: dict[str, int]
    action_accuracy: float
    selection_accuracy: float
    repair_improvement_rate: float
    hard_gate_recovery_rate: float
    issue_counts: dict[str, int]
    failed_case_ids: list[str]


def load_repair_cases(path: str | Path) -> list[RepairEvaluationCase]:
    source = Path(path)
    cases: list[RepairEvaluationCase] = []
    seen: set[str] = set()
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = RepairEvaluationCase.model_validate(json.loads(line))
        except Exception as error:
            raise ValueError(f"invalid repair case at {source}:{line_number}: {error}") from error
        if case.case_id in seen:
            raise ValueError(f"duplicate repair case_id: {case.case_id}")
        seen.add(case.case_id)
        cases.append(case)
    if not cases:
        raise ValueError(f"repair dataset is empty: {source}")
    return cases


def evaluate_repairs(
    cases: list[RepairEvaluationCase],
    *,
    dataset_version: str = "domain-real-repair-v1",
    min_improvement_delta: float = 0.05,
) -> RepairEvaluationReport:
    if not cases:
        raise ValueError("at least one repair evaluation case is required")
    policy = RepairSelectionPolicy(min_improvement_delta)
    action_correct = 0
    selection_correct = 0
    improved = 0
    recoverable = 0
    recovered = 0
    failed: list[str] = []
    for case in cases:
        issue = QualityIssue(
            issue_type=case.issue_type,
            severity="error" if not case.first.passed_hard_gates else "warning",
            target_path=case.target_path,
            message="fixture issue",
            recommended_action="fixture repair",
        )
        observed_action = issue.repairability
        action_ok = observed_action == case.expected_action
        action_correct += action_ok
        first = _quality(case.first, [issue])
        retry = _quality(case.retry, [])
        record = RepairRecord(
            triggered=True,
            actions=[
                RepairActionRecord(
                    action_id=f"action-{case.case_id}",
                    action_type=case.expected_action,
                    status="applied",
                    issue_ids=[str(issue.issue_id)],
                    target_paths=[case.target_path],
                )
            ],
        )
        decision = policy.decide(first, retry, record)
        selection_ok = decision.selected_attempt == case.expected_selected_attempt
        selection_correct += selection_ok
        improved += decision.decision == "repaired_selected"
        if not first.passed_hard_gates:
            recoverable += 1
            recovered += retry.passed_hard_gates
        if not action_ok or not selection_ok:
            failed.append(case.case_id)
    return RepairEvaluationReport(
        dataset_version=dataset_version,
        case_count=len(cases),
        annotation_status_counts=dict(sorted(Counter(case.annotation_status for case in cases).items())),
        action_accuracy=round(action_correct / len(cases), 6),
        selection_accuracy=round(selection_correct / len(cases), 6),
        repair_improvement_rate=round(improved / len(cases), 6),
        hard_gate_recovery_rate=round(recovered / recoverable, 6) if recoverable else 0.0,
        issue_counts=dict(sorted(Counter(case.issue_type for case in cases).items())),
        failed_case_ids=failed,
    )


def _quality(snapshot: RepairQualitySnapshot, issues: list[QualityIssue]) -> ContentQuality:
    return ContentQuality(
        score=snapshot.score,
        threshold=0.75,
        passed_hard_gates=snapshot.passed_hard_gates,
        dimensions=snapshot.dimensions,
        issues=issues,
    )
