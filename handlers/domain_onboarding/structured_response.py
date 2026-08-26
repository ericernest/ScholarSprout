"""Schema-aware normalization for structured model responses.

The model-facing JSON syntax parser deliberately remains small and strict.  This
module owns the next boundary: locating the most plausible response envelope,
normalizing declared aliases, and applying only contract-approved coercions.
It never invents business content or evidence identifiers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from typing import Any, Literal


LOGGER = logging.getLogger(__name__)

ValueKind = Literal["any", "object", "array", "string", "number", "boolean"]


class StructuredResponseError(ValueError):
    """Raised when no unambiguous payload satisfies a response contract."""


@dataclass(frozen=True)
class FieldRule:
    kind: ValueKind
    required: bool = False
    aliases: tuple[str, ...] = ()
    item_kind: ValueKind = "any"
    singleton_to_array: bool = False
    singleton_array_to_value: bool = False
    drop_invalid_items: bool = False


@dataclass(frozen=True)
class ResponseContract:
    name: str
    fields: dict[str, FieldRule]
    wrappers: tuple[str, ...] = ("result", "data", "output")
    max_depth: int = 2
    direct_field: str | None = None
    direct_required_keys: tuple[str, ...] = ()
    reject_ambiguity: bool = True


@dataclass(frozen=True)
class AdaptationEvent:
    code: str
    field: str = ""
    source: str = ""
    detail: str = ""


@dataclass
class AdaptedResponse:
    data: dict[str, Any]
    contract: str
    source_path: str
    score: float
    events: list[AdaptationEvent] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.events) or self.source_path != "$"


@dataclass(frozen=True)
class _Candidate:
    value: dict[str, Any]
    path: str
    ancestors: tuple[dict[str, Any], ...]
    depth: int


@dataclass
class _EvaluatedCandidate:
    candidate: _Candidate
    data: dict[str, Any]
    score: float
    required_matches: int
    missing_required: list[str]
    events: list[AdaptationEvent]


def _matches_kind(value: Any, kind: ValueKind) -> bool:
    if kind == "any":
        return True
    if kind == "object":
        return isinstance(value, dict)
    if kind == "array":
        return isinstance(value, list)
    if kind == "string":
        return isinstance(value, str)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return False


def _coerce_value(
    value: Any,
    rule: FieldRule,
) -> tuple[bool, Any, list[AdaptationEvent]]:
    events: list[AdaptationEvent] = []
    if _matches_kind(value, rule.kind):
        normalized = value
    elif rule.kind == "array" and rule.singleton_to_array and _matches_kind(
        value, rule.item_kind
    ):
        normalized = [value]
        events.append(AdaptationEvent(code="singleton_to_array"))
    elif (
        rule.kind != "array"
        and rule.singleton_array_to_value
        and isinstance(value, list)
        and len(value) == 1
        and _matches_kind(value[0], rule.kind)
    ):
        normalized = value[0]
        events.append(AdaptationEvent(code="singleton_array_to_value"))
    else:
        return False, value, events

    if rule.kind == "array" and rule.item_kind != "any":
        assert isinstance(normalized, list)
        valid_items = [
            item for item in normalized if _matches_kind(item, rule.item_kind)
        ]
        invalid_count = len(normalized) - len(valid_items)
        if invalid_count:
            if not rule.drop_invalid_items:
                return False, value, []
            normalized = valid_items
            events.append(
                AdaptationEvent(
                    code="invalid_array_items_dropped",
                    detail=str(invalid_count),
                )
            )
    return True, normalized, events


def _walk_candidates(
    payload: dict[str, Any], contract: ResponseContract
) -> list[_Candidate]:
    candidates: list[_Candidate] = []

    def visit(
        value: dict[str, Any],
        path: str,
        ancestors: tuple[dict[str, Any], ...],
        depth: int,
    ) -> None:
        candidates.append(_Candidate(value, path, ancestors, depth))
        if depth >= contract.max_depth:
            return
        for key, nested in value.items():
            if not isinstance(nested, dict):
                continue
            # Known wrappers are preferred but all bounded object children are
            # considered so providers may introduce a harmless new envelope.
            visit(nested, f"{path}.{key}", (*ancestors, value), depth + 1)

    visit(payload, "$", (), 0)
    return candidates


def _sources(candidate: _Candidate) -> list[tuple[str, dict[str, Any]]]:
    sources = [(candidate.path, candidate.value)]
    for index, ancestor in enumerate(reversed(candidate.ancestors), start=1):
        sources.append((f"{candidate.path}:ancestor-{index}", ancestor))
    return sources


def _resolve_field(
    candidate: _Candidate,
    name: str,
    rule: FieldRule,
) -> tuple[bool, Any, list[AdaptationEvent]]:
    wrong_type_sources: list[str] = []
    for source_path, source in _sources(candidate):
        for key in (name, *rule.aliases):
            if key not in source:
                continue
            valid, normalized, events = _coerce_value(source[key], rule)
            if not valid:
                wrong_type_sources.append(f"{source_path}.{key}")
                continue
            annotated = [
                AdaptationEvent(
                    code=event.code,
                    field=name,
                    source=f"{source_path}.{key}",
                    detail=event.detail,
                )
                for event in events
            ]
            if key != name:
                annotated.insert(
                    0,
                    AdaptationEvent(
                        code="alias_applied",
                        field=name,
                        source=f"{source_path}.{key}",
                        detail=key,
                    ),
                )
            if wrong_type_sources:
                annotated.insert(
                    0,
                    AdaptationEvent(
                        code="wrong_type_candidate_skipped",
                        field=name,
                        source=wrong_type_sources[0],
                    ),
                )
            return True, normalized, annotated
    events = [
        AdaptationEvent(
            code="wrong_type_field",
            field=name,
            source=wrong_type_sources[0],
        )
    ] if wrong_type_sources else []
    return False, None, events


def _evaluate(candidate: _Candidate, contract: ResponseContract) -> _EvaluatedCandidate:
    score = -candidate.depth * 0.25
    declared_keys = {
        key
        for name, rule in contract.fields.items()
        for key in (name, *rule.aliases)
    }
    data = {
        key: value
        for key, value in candidate.value.items()
        if key not in declared_keys
        and not (key in contract.wrappers and isinstance(value, dict))
    }
    events: list[AdaptationEvent] = []
    missing_required: list[str] = []
    required_matches = 0

    direct_match = bool(
        contract.direct_field
        and contract.direct_required_keys
        and all(key in candidate.value for key in contract.direct_required_keys)
    )
    for name, rule in contract.fields.items():
        found, normalized, field_events = _resolve_field(candidate, name, rule)
        events.extend(field_events)
        if not found and direct_match and name == contract.direct_field:
            normalized = candidate.value
            found = True
            events.append(
                AdaptationEvent(
                    code="direct_object_wrapped",
                    field=name,
                    source=candidate.path,
                )
            )
        if found:
            data[name] = normalized
            if rule.required:
                required_matches += 1
                score += 12.0
            else:
                score += 3.0
            if field_events:
                score -= 0.5
        elif rule.required:
            missing_required.append(name)
            score -= 16.0
        elif field_events:
            score -= 1.0

    if candidate.path.split(".")[-1] in contract.wrappers:
        score += 0.25
    return _EvaluatedCandidate(
        candidate=candidate,
        data=data,
        score=score,
        required_matches=required_matches,
        missing_required=missing_required,
        events=events,
    )


def adapt_structured_response(
    payload: dict[str, Any], contract: ResponseContract
) -> AdaptedResponse:
    """Normalize one parsed JSON object against a bounded response contract."""

    if not isinstance(payload, dict):
        raise StructuredResponseError(
            f"{contract.name} response must be a JSON object"
        )
    evaluated = [_evaluate(item, contract) for item in _walk_candidates(payload, contract)]
    evaluated.sort(
        key=lambda item: (
            len(item.missing_required) == 0,
            item.required_matches,
            item.score,
            -item.candidate.depth,
        ),
        reverse=True,
    )
    selected = evaluated[0]
    if selected.missing_required:
        missing = ", ".join(selected.missing_required)
        raise StructuredResponseError(
            f"{contract.name} response is missing required fields: {missing}"
        )

    if contract.reject_ambiguity and len(evaluated) > 1:
        runner_up = evaluated[1]
        same_quality = (
            not runner_up.missing_required
            and runner_up.required_matches == selected.required_matches
            and abs(runner_up.score - selected.score) < 0.01
        )
        if same_quality:
            selected_json = json.dumps(
                {
                    key: selected.data.get(key)
                    for key in contract.fields
                    if key in selected.data
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            runner_json = json.dumps(
                {
                    key: runner_up.data.get(key)
                    for key in contract.fields
                    if key in runner_up.data
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            if selected_json != runner_json:
                raise StructuredResponseError(
                    f"{contract.name} response has ambiguous matching objects at "
                    f"{selected.candidate.path} and {runner_up.candidate.path}"
                )

    result = AdaptedResponse(
        data=selected.data,
        contract=contract.name,
        source_path=selected.candidate.path,
        score=round(selected.score, 3),
        events=selected.events,
    )
    if result.changed:
        LOGGER.info(
            "normalized structured response contract=%s source=%s events=%s",
            result.contract,
            result.source_path,
            [event.code for event in result.events],
        )
    return result
