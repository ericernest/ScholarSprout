from __future__ import annotations

import argparse

from config.manager import load_config
from handlers.domain_onboarding.pipeline import create_default_pipeline
from models.client import OpenAIClient

from .online import (
    OnlineRunLimits,
    load_online_cases,
    run_online_evaluation,
    validate_online_permission,
    write_online_report,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run controlled online domain onboarding tests")
    parser.add_argument("dataset")
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-cases", type=int, default=2)
    parser.add_argument("--max-estimated-cost-usd", type=float, default=0.5)
    parser.add_argument("--cost-reserve-per-case-usd", type=float, default=0.25)
    parser.add_argument("--confirm-online", action="store_true")
    parser.add_argument("--allow-unpriced", action="store_true")
    args = parser.parse_args()

    app_config = load_config()
    validate_online_permission(
        confirmed=args.confirm_online,
        input_cost_per_million_tokens=app_config.client.input_cost_per_million_tokens,
        output_cost_per_million_tokens=app_config.client.output_cost_per_million_tokens,
        allow_unpriced=args.allow_unpriced,
    )
    if not app_config.client.api_key.strip() or not app_config.client.model_name.strip():
        raise RuntimeError("a real API key and model_name must be configured")
    model = OpenAIClient(app_config.client)
    pipeline = create_default_pipeline(model)
    try:
        report = run_online_evaluation(
            pipeline,
            load_online_cases(args.dataset),
            OnlineRunLimits(
                max_cases=args.max_cases,
                max_estimated_cost_usd=args.max_estimated_cost_usd,
                cost_reserve_per_case_usd=args.cost_reserve_per_case_usd,
            ),
            input_cost_per_million_tokens=app_config.client.input_cost_per_million_tokens,
            output_cost_per_million_tokens=app_config.client.output_cost_per_million_tokens,
        )
        write_online_report(report, args.output)
        print(report.model_dump_json(indent=2))
    finally:
        pipeline.close()


if __name__ == "__main__":
    main()
