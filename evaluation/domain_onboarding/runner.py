from __future__ import annotations

import json
from pathlib import Path

from .dataset import load_cases
from .metrics import evaluate_cases
from .schemas import OfflineEvaluationReport


def run_offline_evaluation(
    dataset: str | Path,
    *,
    dataset_version: str = "domain-onboarding-human-v1",
    output: str | Path | None = None,
) -> OfflineEvaluationReport:
    report = evaluate_cases(load_cases(dataset), dataset_version=dataset_version)
    if output is not None:
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )
    return report
