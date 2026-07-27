from __future__ import annotations

import argparse

from .runner import run_offline_evaluation


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate domain onboarding quality records")
    parser.add_argument("dataset")
    parser.add_argument("--dataset-version", default="domain-onboarding-human-v1")
    parser.add_argument("--output")
    args = parser.parse_args()
    report = run_offline_evaluation(
        args.dataset,
        dataset_version=args.dataset_version,
        output=args.output,
    )
    print(report.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
