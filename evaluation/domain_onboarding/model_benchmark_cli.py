"""Run an end-to-end and frozen-input chat-model benchmark."""

from __future__ import annotations

import argparse
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from config.manager import load_config
from handlers.domain_onboarding.config import DomainOnboardingConfig
from handlers.domain_onboarding.pipeline import (
    DomainOnboardingPipeline,
    create_default_pipeline,
)
from handlers.domain_onboarding.schemas import RankedPaper
from models.client import OpenAIClient

from .model_benchmark import (
    BenchmarkMode,
    FrozenBenchmarkCase,
    FrozenCoverageAnalyzer,
    FrozenPlanner,
    FrozenRanker,
    FrozenRetriever,
    append_run,
    benchmark_run_key,
    build_schedule,
    is_resumable_complete,
    is_transient_infrastructure_failure,
    load_completed_runs,
    run_benchmark_case,
    write_benchmark_outputs,
)
from .online import load_online_cases, validate_online_permission


DEFAULT_MODELS = ["qwen3.6-chat", "deepseek-v4-flash", "glm-5.2"]
DEFAULT_CASE_IDS = ["multi-agent-debate-zh", "rag-zh"]
ROUTE_ENV_NAMES = (
    "DOMAIN_ONBOARDING_PLANNING_MODELS",
    "DOMAIN_ONBOARDING_STAGE_PLANNING_MODELS",
    "DOMAIN_ONBOARDING_GENERATION_MODELS",
    "DOMAIN_ONBOARDING_DEVELOPMENT_MODELS",
    "DOMAIN_ONBOARDING_LANDSCAPE_MODELS",
    "DOMAIN_ONBOARDING_LEARNING_PATH_MODELS",
    "DOMAIN_ONBOARDING_REPAIR_MODELS",
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark chat models with real and frozen domain-onboarding inputs"
    )
    parser.add_argument("dataset")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--case-ids", nargs="+", default=DEFAULT_CASE_IDS)
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=("end_to_end", "model_only"),
        default=["end_to_end", "model_only"],
    )
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--seed", type=int, default=20260811)
    parser.add_argument("--confirm-online", action="store_true")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--transient-retries", type=int, default=2)
    parser.add_argument("--transient-backoff-seconds", type=float, default=10.0)
    args = parser.parse_args()

    app_config = load_config()
    validate_online_permission(
        confirmed=args.confirm_online,
        input_cost_per_million_tokens=app_config.client.input_cost_per_million_tokens,
        output_cost_per_million_tokens=app_config.client.output_cost_per_million_tokens,
        allow_unpriced=True,
    )
    if not app_config.client.api_key.strip() or not app_config.client.base_url:
        raise RuntimeError("a real API key and base_url must be configured")
    if args.repeats < 1:
        raise ValueError("--repeats must be positive")
    if args.transient_retries < 0 or args.transient_backoff_seconds < 0:
        raise ValueError("transient retry settings must be non-negative")
    models = list(dict.fromkeys(item.strip() for item in args.models if item.strip()))
    if not models:
        raise ValueError("at least one model is required")
    available = {case.case_id: case for case in load_online_cases(args.dataset)}
    missing = [case_id for case_id in args.case_ids if case_id not in available]
    if missing:
        raise ValueError(f"unknown case ids: {', '.join(missing)}")
    cases = [available[case_id] for case_id in args.case_ids]
    modes: list[BenchmarkMode] = list(dict.fromkeys(args.modes))  # type: ignore[assignment]
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / "model-benchmark-runs.jsonl"
    frozen_path = output_dir / "frozen-model-inputs.json"
    if args.reset:
        raw_path.unlink(missing_ok=True)
        frozen_path.unlink(missing_ok=True)

    frozen_cases: dict[str, FrozenBenchmarkCase] = {}
    if "model_only" in modes:
        if frozen_path.exists():
            frozen_cases = _load_frozen_cases(frozen_path)
        else:
            print("PREPARE frozen inputs", flush=True)
            frozen_cases = _prepare_frozen_cases(
                app_config,
                cases,
                DomainOnboardingConfig(),
            )
            frozen_path.write_text(
                json.dumps(
                    {
                        case_id: item.model_dump(mode="json")
                        for case_id, item in frozen_cases.items()
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            print(f"PREPARED {frozen_path}", flush=True)

    completed_by_key = {
        run.run_key: run for run in load_completed_runs(raw_path)
    }
    completed_keys = {
        run_key
        for run_key, run in completed_by_key.items()
        if is_resumable_complete(run)
    }
    schedule = build_schedule(
        modes=modes,
        models=models,
        cases=cases,
        repeats=args.repeats,
        seed=args.seed,
    )
    print(
        f"MATRIX total={len(schedule)} completed={len(completed_keys)} pending="
        f"{sum(benchmark_run_key(*item[:2], item[2].case_id, item[3]) not in completed_keys for item in schedule)}",
        flush=True,
    )
    settings = DomainOnboardingConfig()
    for index, (mode, model_name, case, repeat) in enumerate(schedule, start=1):
        run_key = benchmark_run_key(mode, model_name, case.case_id, repeat)
        if run_key in completed_keys:
            continue
        print(
            f"START {index}/{len(schedule)} {run_key}",
            flush=True,
        )
        for attempt in range(1, args.transient_retries + 2):
            model = OpenAIClient(app_config.client)
            pipeline: DomainOnboardingPipeline | None = None
            try:
                with _model_environment(
                    model_name,
                    embedding_enabled=mode == "end_to_end",
                    embedding_model=app_config.embedding.model_name,
                ):
                    pipeline = _create_pipeline(
                        model,
                        settings,
                        app_config.embedding.model_name,
                        frozen_cases.get(case.case_id)
                        if mode == "model_only"
                        else None,
                    )
                    run = run_benchmark_case(
                        pipeline,
                        mode=mode,
                        model=model_name,
                        case=case,
                        repeat=repeat,
                    )
            finally:
                if pipeline is not None:
                    pipeline.close()
                _close_model(model)
            if not is_transient_infrastructure_failure(run):
                break
            if attempt > args.transient_retries:
                break
            delay = args.transient_backoff_seconds * attempt
            print(
                f"RETRY {run_key} transient_connection_failure "
                f"attempt={attempt + 1} delay_seconds={delay}",
                flush=True,
            )
            time.sleep(delay)
        append_run(raw_path, run)
        completed_by_key[run.run_key] = run
        completed_keys.add(run.run_key)
        write_benchmark_outputs(output_dir, list(completed_by_key.values()))
        print(
            f"DONE {run.run_key} status={run.status} duration_ms={run.duration_ms} "
            f"tokens={run.total_tokens} quality={run.quality_score}",
            flush=True,
        )

    completed = list(completed_by_key.values())
    details, summary, report = write_benchmark_outputs(output_dir, completed)
    print(f"COMPLETE runs={len(completed)}", flush=True)
    print(f"DETAILS {details}", flush=True)
    print(f"SUMMARY {summary}", flush=True)
    print(f"REPORT {report}", flush=True)


def _prepare_frozen_cases(
    app_config: object,
    cases: list[object],
    settings: DomainOnboardingConfig,
) -> dict[str, FrozenBenchmarkCase]:
    model = OpenAIClient(app_config.client)  # type: ignore[attr-defined]
    pipeline: DomainOnboardingPipeline | None = None
    try:
        with _model_environment(
            app_config.client.model_name,  # type: ignore[attr-defined]
            embedding_enabled=True,
            embedding_model=app_config.embedding.model_name,  # type: ignore[attr-defined]
        ):
            pipeline = create_default_pipeline(
                model,
                settings,
                embedding_model=model,
                embedding_model_name=app_config.embedding.model_name,  # type: ignore[attr-defined]
            )
            prepared: dict[str, FrozenBenchmarkCase] = {}
            for case in cases:
                plan = pipeline.planner._fallback_plan(case.query)
                retrieval = pipeline.retriever.search(
                    plan.search_queries,
                    limit_per_query=settings.papers_per_query,
                )
                candidates = pipeline._annotate_candidate_query_hints(
                    retrieval.papers,
                    plan.paper_queries,
                )
                ranking = pipeline.ranker.rank(
                    candidates,
                    plan,
                    limit=settings.selected_paper_limit,
                )
                if not ranking.papers:
                    raise RuntimeError(
                        f"frozen input preparation returned no papers for {case.case_id}"
                    )
                prepared[case.case_id] = FrozenBenchmarkCase(
                    case=case,
                    plan=plan,
                    papers=ranking.papers,
                )
                print(
                    f"FROZEN {case.case_id} papers={len(ranking.papers)}",
                    flush=True,
                )
            return prepared
    finally:
        if pipeline is not None:
            pipeline.close()
        _close_model(model)


def _create_pipeline(
    model: OpenAIClient,
    settings: DomainOnboardingConfig,
    embedding_model_name: str,
    frozen: FrozenBenchmarkCase | None,
) -> DomainOnboardingPipeline:
    base = create_default_pipeline(
        model,
        settings,
        embedding_model=model,
        embedding_model_name=embedding_model_name,
    )
    if frozen is None:
        return base
    base.close()
    return DomainOnboardingPipeline(
        profile_builder=base.profile_builder,
        planner=FrozenPlanner(frozen.plan),
        retriever=FrozenRetriever(frozen.papers),
        ranker=FrozenRanker(frozen.papers),
        coverage_analyzer=FrozenCoverageAnalyzer(),
        generator=base.generator,
        evaluator=base.evaluator,
        repairer=base.repairer,
        config=settings,
    )


def _load_frozen_cases(path: Path) -> dict[str, FrozenBenchmarkCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        case_id: FrozenBenchmarkCase.model_validate(item)
        for case_id, item in payload.items()
    }


@contextmanager
def _model_environment(
    model_name: str,
    *,
    embedding_enabled: bool,
    embedding_model: str,
) -> Iterator[None]:
    updates = {
        **{name: model_name for name in ROUTE_ENV_NAMES},
        "DOMAIN_ONBOARDING_EMBEDDING_ENABLED": (
            "true" if embedding_enabled else "false"
        ),
        "DOMAIN_ONBOARDING_EMBEDDING_MODEL": embedding_model,
    }
    previous = {name: os.environ.get(name) for name in updates}
    os.environ.update(updates)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _close_model(model: OpenAIClient) -> None:
    close = getattr(model.client, "close", None)
    if callable(close):
        close()


if __name__ == "__main__":
    main()
