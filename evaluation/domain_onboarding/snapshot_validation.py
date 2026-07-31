"""Validation for checked-in snapshots captured from the real async pipeline."""

from __future__ import annotations

from typing import Any

from handlers.domain_onboarding.schemas import (
    ContentQuality,
    DomainOnboardingOutput,
    RankedPaper,
)


PLACEHOLDER_VALUES = {
    "...",
    "…",
    "todo",
    "tbd",
    "placeholder",
    "f1234567890abcde",
}
REPRODUCIBILITY_KEYS = {
    "policy_version",
    "policy_fingerprint",
    "request_id",
    "search_queries",
    "retrieval_sources",
    "selected_paper_ids",
    "ranking_vectorizer_backend",
    "ranking_vectorizer_fallback_used",
    "canonical_registry_version",
}


def validate_completed_snapshot(
    snapshot: dict[str, Any], *, evaluator: Any | None = None
) -> list[str]:
    """Return deterministic contract errors; an empty list means publishable."""
    errors: list[str] = []
    if snapshot.get("state") != "completed":
        errors.append("snapshot.state must be completed")
    if snapshot.get("partial_result") != {}:
        errors.append("completed snapshot must clear partial_result")
    result = snapshot.get("result")
    if not isinstance(result, dict):
        return [*errors, "snapshot.result must be an object"]

    placeholders = _placeholder_paths(result)
    errors.extend(f"placeholder value at {path}" for path in placeholders)
    try:
        output = DomainOnboardingOutput.model_validate(result)
        quality = ContentQuality.model_validate(result.get("quality"))
    except Exception as error:
        return [*errors, f"schema validation failed: {error}"]

    if not quality.hard_gates:
        errors.append("quality.hard_gates must contain evaluated gates")
    if not quality.passed_hard_gates:
        errors.append("publishable snapshot must pass every quality hard gate")
    errors.extend(_status_errors(result.get("status"), quality))

    expected_paper_ids = [paper.paper_id for paper in output.papers]
    selected_ids = output.reproducibility.get("selected_paper_ids")
    if selected_ids != expected_paper_ids:
        errors.append("reproducibility.selected_paper_ids must match papers in order")
    missing_reproducibility = sorted(
        REPRODUCIBILITY_KEYS - set(output.reproducibility)
    )
    if missing_reproducibility:
        errors.append(
            "reproducibility is missing: " + ", ".join(missing_reproducibility)
        )

    paper_by_id = {paper.paper_id: paper for paper in output.papers}
    for index, step in enumerate(output.learning_path):
        referenced_ids = [paper.paper_id for paper in step.papers]
        if referenced_ids != step.paper_ids:
            errors.append(
                f"learning_path[{index}].papers must hydrate paper_ids in order"
            )
        if any(paper_id not in paper_by_id for paper_id in step.paper_ids):
            errors.append(f"learning_path[{index}] contains an unknown paper_id")

    if evaluator is not None:
        allowed = [
            RankedPaper(
                **paper.model_dump(),
                matched_queries=output.research_plan.search_queries,
            )
            for paper in output.papers
        ]
        reevaluated = evaluator.evaluate(output, allowed)
        errors.extend(_quality_drift_errors(quality, reevaluated))
    return errors


def _status_errors(status: Any, quality: ContentQuality) -> list[str]:
    if not quality.passed_hard_gates:
        expected = "quality_failed"
    elif quality.score < quality.threshold or quality.issues:
        expected = "quality_warning"
    else:
        expected = "ok"
    return [] if status == expected else [f"result.status must be {expected}, got {status}"]


def _quality_drift_errors(
    declared: ContentQuality, reevaluated: ContentQuality
) -> list[str]:
    errors = []
    if declared.passed_hard_gates != reevaluated.passed_hard_gates:
        errors.append("declared hard-gate result differs from current evaluator")
    if declared.state != reevaluated.state:
        errors.append(
            f"declared quality state {declared.state} differs from {reevaluated.state}"
        )
    if abs(declared.score - reevaluated.score) > 1e-6:
        errors.append(
            f"declared quality score {declared.score} differs from {reevaluated.score}"
        )
    if declared.dimensions != reevaluated.dimensions:
        errors.append("declared quality dimensions differ from current evaluator")
    declared_issues = [
        issue.model_dump(mode="json", exclude={"issue_id"})
        for issue in declared.issues
    ]
    reevaluated_issues = [
        issue.model_dump(mode="json", exclude={"issue_id"})
        for issue in reevaluated.issues
    ]
    if declared_issues != reevaluated_issues:
        errors.append("declared quality issues differ from current evaluator")
    return errors


def _placeholder_paths(value: Any, path: str = "result") -> list[str]:
    if isinstance(value, str):
        return [path] if value.strip().lower() in PLACEHOLDER_VALUES else []
    if isinstance(value, list):
        return [
            item
            for index, child in enumerate(value)
            for item in _placeholder_paths(child, f"{path}[{index}]")
        ]
    if isinstance(value, dict):
        return [
            item
            for key, child in value.items()
            for item in _placeholder_paths(child, f"{path}.{key}")
        ]
    return []
