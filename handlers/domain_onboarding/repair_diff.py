"""为修复候选结果生成稳定指纹并校验定向字段变更。"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any

from .schemas import DomainOnboardingOutput


def fingerprint_output(output: DomainOnboardingOutput) -> str:
    payload = json.dumps(
        output.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def changed_output_paths(
    before: DomainOnboardingOutput,
    after: DomainOnboardingOutput,
) -> list[str]:
    changed: set[str] = set()
    _collect_changes(
        before.model_dump(mode="json"),
        after.model_dump(mode="json"),
        "$",
        changed,
    )
    return sorted(changed)


def paths_outside_targets(
    changed_paths: list[str],
    target_paths: list[str],
) -> list[str]:
    normalized_targets = [_normalize_target(path) for path in target_paths]
    if "$" in normalized_targets:
        return []
    return [
        path
        for path in changed_paths
        if not any(_is_within(path, target) for target in normalized_targets)
    ]


def apply_targeted_changes(
    before: DomainOnboardingOutput,
    candidate: DomainOnboardingOutput,
    target_paths: list[str],
) -> DomainOnboardingOutput:
    """Copy only explicitly authorized candidate subtrees into the prior output."""
    merged = before.model_dump(mode="json")
    source = candidate.model_dump(mode="json")
    for target in target_paths:
        tokens = _path_tokens(_normalize_target(target))
        if not tokens:
            merged = deepcopy(source)
            continue
        try:
            value = deepcopy(_get_path(source, tokens))
            _set_path(merged, tokens, value)
        except (IndexError, KeyError, TypeError):
            continue
    return DomainOnboardingOutput.model_validate(merged)


def _path_tokens(path: str) -> list[str | int]:
    if path == "$":
        return []
    return [
        int(index) if index else name
        for name, index in re.findall(r"(?:^\$\.|\.)([^.\[]+)|\[(\d+)\]", path)
    ]


def _get_path(value: Any, tokens: list[str | int]) -> Any:
    current = value
    for token in tokens:
        current = current[token]
    return current


def _set_path(value: Any, tokens: list[str | int], replacement: Any) -> None:
    current = value
    for token in tokens[:-1]:
        current = current[token]
    current[tokens[-1]] = replacement


def _collect_changes(before: Any, after: Any, path: str, changed: set[str]) -> None:
    if type(before) is not type(after):
        changed.add(path)
        return
    if isinstance(before, dict):
        for key in sorted(set(before) | set(after)):
            child = f"{path}.{key}"
            if key not in before or key not in after:
                changed.add(child)
            else:
                _collect_changes(before[key], after[key], child, changed)
        return
    if isinstance(before, list):
        if len(before) != len(after):
            changed.add(path)
        for index, (before_item, after_item) in enumerate(zip(before, after)):
            _collect_changes(before_item, after_item, f"{path}[{index}]", changed)
        return
    if before != after:
        changed.add(path)


def _normalize_target(path: str) -> str:
    stripped = path.strip()
    if stripped in {"", "$"}:
        return "$"
    if stripped.startswith("$"):
        return stripped
    return f"$.{stripped}"


def _is_within(path: str, target: str) -> bool:
    return path == target or path.startswith(f"{target}.") or path.startswith(f"{target}[")
