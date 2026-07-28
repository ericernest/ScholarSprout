from __future__ import annotations

import json
from pathlib import Path

from .schemas import OfflineEvaluationCase


def load_cases(path: str | Path) -> list[OfflineEvaluationCase]:
    source = Path(path)
    cases: list[OfflineEvaluationCase] = []
    seen: set[str] = set()
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = OfflineEvaluationCase.model_validate(json.loads(line))
        except Exception as error:
            raise ValueError(f"invalid evaluation case at {source}:{line_number}: {error}") from error
        if case.case_id in seen:
            raise ValueError(f"duplicate evaluation case_id: {case.case_id}")
        seen.add(case.case_id)
        cases.append(case)
    if not cases:
        raise ValueError(f"evaluation dataset is empty: {source}")
    return cases
