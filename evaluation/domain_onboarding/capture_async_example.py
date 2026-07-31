"""Capture a real, validated async snapshot and replayable event log."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from config.manager import load_config
from handlers.domain_onboarding.jobs import DomainOnboardingJobManager, SQLiteJobStore
from handlers.domain_onboarding.pipeline import create_default_pipeline
from handlers.domain_onboarding.schemas import DomainOnboardingRequest
from models.client import OpenAIClient

from .online import validate_online_permission
from .snapshot_validation import validate_completed_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Capture a controlled real async domain-onboarding example"
    )
    parser.add_argument("request")
    parser.add_argument("--snapshot-output", required=True)
    parser.add_argument("--events-output", required=True)
    parser.add_argument("--confirm-online", action="store_true")
    parser.add_argument("--allow-unpriced", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=360.0)
    parser.add_argument("--model-name")
    args = parser.parse_args()

    app_config = load_config()
    if args.model_name:
        app_config.client = replace(app_config.client, model_name=args.model_name)
    validate_online_permission(
        confirmed=args.confirm_online,
        input_cost_per_million_tokens=app_config.client.input_cost_per_million_tokens,
        output_cost_per_million_tokens=app_config.client.output_cost_per_million_tokens,
        allow_unpriced=args.allow_unpriced,
    )
    payload = json.loads(Path(args.request).read_text(encoding="utf-8"))
    client_request_id = payload.pop("client_request_id", None)
    request = DomainOnboardingRequest.model_validate(payload)
    model = OpenAIClient(app_config.client)
    pipeline = create_default_pipeline(model)
    try:
        with tempfile.TemporaryDirectory(prefix="domain-onboarding-capture-") as directory:
            store = SQLiteJobStore(Path(directory) / "jobs.sqlite3")
            manager = DomainOnboardingJobManager(
                pipeline,
                store,
                max_workers=1,
                max_queue_size=0,
                token_secret="controlled-example-capture",
            )
            try:
                job = manager.submit(
                    request,
                    client_request_id=client_request_id,
                    owner_key="controlled-example",
                )
                deadline = time.monotonic() + args.timeout_seconds
                while time.monotonic() < deadline:
                    snapshot = store.get(job["task_id"])
                    if snapshot and snapshot["state"] in {
                        "completed",
                        "failed",
                        "cancelled",
                        "interrupted",
                    }:
                        break
                    time.sleep(0.25)
                else:
                    manager.cancel(job["task_id"])
                    snapshot = store.get(job["task_id"])
                    events = store.events_after(job["task_id"], 0)
                    _write_json(args.snapshot_output, snapshot)
                    _write_jsonl(args.events_output, events)
                    raise TimeoutError("async example capture exceeded its deadline")
                events = store.events_after(job["task_id"], 0)
                _persist_terminal_capture(
                    snapshot,
                    events,
                    snapshot_output=args.snapshot_output,
                    events_output=args.events_output,
                )
                if snapshot["state"] != "completed":
                    raise RuntimeError(
                        f"real async request ended in {snapshot['state']}: {snapshot['error']}"
                    )
                errors = validate_completed_snapshot(
                    snapshot, evaluator=pipeline.evaluator
                )
                if errors:
                    raise RuntimeError(
                        "captured snapshot failed publication checks:\n- "
                        + "\n- ".join(errors)
                    )
            finally:
                manager.close()
    finally:
        pipeline.close()


def _write_json(path: str | Path, value: object) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _write_jsonl(path: str | Path, values: list[dict[str, object]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values),
        encoding="utf-8",
    )


def _persist_terminal_capture(
    snapshot: dict[str, object],
    events: list[dict[str, object]],
    *,
    snapshot_output: str | Path,
    events_output: str | Path,
) -> None:
    """Persist the exact terminal run before deciding whether it is publishable."""
    for event in events:
        data = event.get("data")
        if (
            event.get("event") == "completed"
            and isinstance(data, dict)
            and data.get("result_available")
        ):
            data["result"] = snapshot.get("result")
    _write_json(snapshot_output, snapshot)
    _write_jsonl(events_output, events)


if __name__ == "__main__":
    main()
