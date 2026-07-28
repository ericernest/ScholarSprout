from __future__ import annotations

from collections import defaultdict
from collections import Counter
from statistics import fmean

from handlers.domain_onboarding.policy import default_dimension_weights

from .schemas import (
    DomainEvaluationSummary,
    OfflineEvaluationCase,
    OfflineEvaluationReport,
    PolicyEvaluationSummary,
)


def evaluate_cases(
    cases: list[OfflineEvaluationCase],
    *,
    dataset_version: str = "domain-onboarding-human-v1",
) -> OfflineEvaluationReport:
    if not cases:
        raise ValueError("at least one evaluation case is required")

    hard_gate_passes = sum(case.first.passed_hard_gates for case in cases)
    hard_gate_agreements = sum(
        case.first.passed_hard_gates == case.human.expected_hard_gate_pass
        for case in cases
    )
    repair_cases = [case for case in cases if case.retry is not None]
    improved_repairs = sum(
        case.selected_attempt == 2
        and case.retry is not None
        and case.retry.score > case.first.score
        and case.retry.passed_hard_gates
        for case in repair_cases
    )
    false_positive_count, predicted_issue_count = _issue_counts(cases)
    dimensions = _common_dimensions(cases)
    dimension_mae = {
        name: _round(
            fmean(
                abs(case.first.dimensions[name] - case.human.dimension_scores[name])
                for case in cases
            )
        )
        for name in dimensions
    }
    stability = {
        name: _round(
            fmean(_dimension_range(case, name) for case in cases)
        )
        for name in dimensions
    }
    grouped: dict[str, list[OfflineEvaluationCase]] = defaultdict(list)
    by_policy: dict[str, list[OfflineEvaluationCase]] = defaultdict(list)
    for case in cases:
        grouped[case.domain].append(case)
        by_policy[case.policy_version].append(case)

    return OfflineEvaluationReport(
        dataset_version=dataset_version,
        policy_versions=sorted({case.policy_version for case in cases}),
        case_count=len(cases),
        annotation_status_counts=dict(
            Counter(case.human.annotation_status for case in cases)
        ),
        hard_gate_pass_rate=_ratio(hard_gate_passes, len(cases)),
        hard_gate_agreement_rate=_ratio(hard_gate_agreements, len(cases)),
        repair_attempt_count=len(repair_cases),
        repair_improved_count=improved_repairs,
        repair_improvement_rate=_ratio(improved_repairs, len(repair_cases)),
        false_positive_count=false_positive_count,
        predicted_issue_count=predicted_issue_count,
        false_positive_rate=_ratio(false_positive_count, predicted_issue_count),
        dimension_mean_absolute_error=dimension_mae,
        dimension_stability_range=stability,
        policy_summaries={
            version: _policy_summary(items)
            for version, items in sorted(by_policy.items())
        },
        domain_summaries={
            domain: _domain_summary(items) for domain, items in sorted(grouped.items())
        },
    )


def _policy_summary(cases: list[OfflineEvaluationCase]) -> PolicyEvaluationSummary:
    base = _domain_summary(cases)
    repair_cases = [case for case in cases if case.retry is not None]
    improved = sum(
        case.selected_attempt == 2
        and case.retry is not None
        and case.retry.score > case.first.score
        and case.retry.passed_hard_gates
        for case in repair_cases
    )
    dimensions = _common_dimensions(cases)
    return PolicyEvaluationSummary(
        **base.model_dump(),
        repair_attempt_count=len(repair_cases),
        repair_improvement_rate=_ratio(improved, len(repair_cases)),
        dimension_mean_absolute_error={
            name: _round(
                fmean(
                    abs(case.first.dimensions[name] - case.human.dimension_scores[name])
                    for case in cases
                )
            )
            for name in dimensions
        },
        dimension_stability_range={
            name: _round(fmean(_dimension_range(case, name) for case in cases))
            for name in dimensions
        },
    )


def _domain_summary(cases: list[OfflineEvaluationCase]) -> DomainEvaluationSummary:
    false_positives, predicted = _issue_counts(cases)
    return DomainEvaluationSummary(
        cases=len(cases),
        hard_gate_pass_rate=_ratio(
            sum(case.first.passed_hard_gates for case in cases), len(cases)
        ),
        hard_gate_agreement_rate=_ratio(
            sum(
                case.first.passed_hard_gates == case.human.expected_hard_gate_pass
                for case in cases
            ),
            len(cases),
        ),
        false_positive_rate=_ratio(false_positives, predicted),
    )


def _issue_counts(cases: list[OfflineEvaluationCase]) -> tuple[int, int]:
    predicted = 0
    false_positives = 0
    for case in cases:
        predicted_types = set(case.first.issue_types)
        expected_types = set(case.human.expected_issue_types)
        predicted += len(predicted_types)
        false_positives += len(predicted_types - expected_types)
    return false_positives, predicted


def _dimension_range(case: OfflineEvaluationCase, dimension: str) -> float:
    observations = case.repeated_dimensions or [case.first.dimensions]
    values = [items[dimension] for items in observations]
    return max(values) - min(values)


def _common_dimensions(cases: list[OfflineEvaluationCase]) -> list[str]:
    available = set.intersection(
        *(set(case.first.dimensions) & set(case.human.dimension_scores) for case in cases)
    )
    return [name for name in default_dimension_weights() if name in available]


def _ratio(numerator: int, denominator: int) -> float:
    return _round(numerator / denominator) if denominator else 0.0


def _round(value: float) -> float:
    return round(float(value), 6)
