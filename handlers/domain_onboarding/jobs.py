"""Persistent asynchronous jobs and replayable incremental events."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Any, Iterator, Protocol
from uuid import uuid4

from .audit import build_audit_record
from .execution import PipelineExecutionContext
from .metrics import DomainOnboardingRequestTrace
from .schemas import DomainOnboardingRequest, PipelineResult


TERMINAL_STATES = {"completed", "failed", "cancelled", "interrupted"}


class JobStore(Protocol):
    def create(self, task_id: str, request: dict[str, Any], client_request_id: str | None) -> dict[str, Any]: ...
    def get(self, task_id: str) -> dict[str, Any] | None: ...
    def append_event(self, task_id: str, event: str, progress: float, provisional: bool, replace_paths: list[str], data: dict[str, Any]) -> dict[str, Any]: ...
    def finish(self, task_id: str, state: str, result: dict[str, Any] | None, error: str | None) -> None: ...
    def request_cancel(self, task_id: str) -> dict[str, Any] | None: ...
    def events_after(self, task_id: str, event_id: int) -> list[dict[str, Any]]: ...
    def recover_interrupted(self) -> int: ...


class SQLiteJobStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                """CREATE TABLE IF NOT EXISTS jobs (
                task_id TEXT PRIMARY KEY, client_request_id TEXT,
                state TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
                current_stage TEXT NOT NULL DEFAULT 'accepted', progress REAL NOT NULL DEFAULT 0,
                request_json TEXT NOT NULL, partial_json TEXT NOT NULL DEFAULT '{}',
                result_json TEXT, error TEXT, retryable INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"""
            )
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS jobs_client_request_id ON jobs(client_request_id) WHERE client_request_id IS NOT NULL"
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS job_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                revision INTEGER NOT NULL, event TEXT NOT NULL, progress REAL NOT NULL,
                provisional INTEGER NOT NULL, replace_paths_json TEXT NOT NULL,
                data_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(task_id) REFERENCES jobs(task_id))"""
            )
            db.execute("CREATE INDEX IF NOT EXISTS job_events_task_id_id ON job_events(task_id, id)")

    def create(self, task_id: str, request: dict[str, Any], client_request_id: str | None) -> dict[str, Any]:
        with self._lock, self._connect() as db:
            if client_request_id:
                existing = db.execute("SELECT * FROM jobs WHERE client_request_id=?", (client_request_id,)).fetchone()
                if existing:
                    return self._row(existing)
            db.execute(
                "INSERT INTO jobs(task_id, client_request_id, state, request_json) VALUES(?,?,?,?)",
                (task_id, client_request_id, "queued", json.dumps(request, ensure_ascii=False)),
            )
        return self.get(task_id) or {}

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE task_id=?", (task_id,)).fetchone()
        return self._row(row) if row else None

    def append_event(self, task_id: str, event: str, progress: float, provisional: bool, replace_paths: list[str], data: dict[str, Any]) -> dict[str, Any]:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT revision, partial_json, state FROM jobs WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            revision = int(row["revision"]) + 1
            partial = json.loads(row["partial_json"] or "{}")
            for path in replace_paths:
                key = path.split(".", 1)[0]
                if key in data and key != "result":
                    partial[key] = data[key]
            cursor = db.execute(
                "INSERT INTO job_events(task_id,revision,event,progress,provisional,replace_paths_json,data_json) VALUES(?,?,?,?,?,?,?)",
                (task_id, revision, event, progress, int(provisional), json.dumps(replace_paths), json.dumps(data, ensure_ascii=False)),
            )
            state = row["state"]
            if state not in TERMINAL_STATES and state != "cancel_requested":
                state = "running" if event != "accepted" else "queued"
            db.execute(
                "UPDATE jobs SET state=?,revision=?,current_stage=?,progress=?,partial_json=?,updated_at=CURRENT_TIMESTAMP WHERE task_id=?",
                (state, revision, event, progress, json.dumps(partial, ensure_ascii=False), task_id),
            )
            event_id = int(cursor.lastrowid)
        return {"id": event_id, "task_id": task_id, "revision": revision, "event": event, "progress": progress, "provisional": provisional, "replace_paths": replace_paths, "data": data}

    def finish(self, task_id: str, state: str, result: dict[str, Any] | None, error: str | None) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "UPDATE jobs SET state=?,progress=CASE WHEN ?='completed' THEN 1.0 ELSE progress END,result_json=?,error=?,retryable=?,partial_json=CASE WHEN ?='completed' THEN '{}' ELSE partial_json END,updated_at=CURRENT_TIMESTAMP WHERE task_id=?",
                (state, state, json.dumps(result, ensure_ascii=False) if result is not None else None, error, int(state in {"failed", "interrupted"}), state, task_id),
            )

    def request_cancel(self, task_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT state FROM jobs WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                return None
            if row["state"] not in TERMINAL_STATES:
                db.execute("UPDATE jobs SET state='cancel_requested',updated_at=CURRENT_TIMESTAMP WHERE task_id=?", (task_id,))
        return self.get(task_id)

    def events_after(self, task_id: str, event_id: int) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM job_events WHERE task_id=? AND id>? ORDER BY id", (task_id, event_id)).fetchall()
        return [self._event_row(row) for row in rows]

    def recover_interrupted(self) -> int:
        with self._lock, self._connect() as db:
            cursor = db.execute(
                "UPDATE jobs SET state='interrupted',error='Gateway restarted before completion.',retryable=1,updated_at=CURRENT_TIMESTAMP WHERE state IN ('queued','running','cancel_requested')"
            )
            return cursor.rowcount

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "task_id": row["task_id"],
            "state": row["state"], "revision": row["revision"],
            "current_stage": row["current_stage"], "progress": row["progress"],
            "request": json.loads(row["request_json"]), "partial_result": json.loads(row["partial_json"] or "{}"),
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "error": row["error"], "retryable": bool(row["retryable"]),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    @staticmethod
    def _event_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "task_id": row["task_id"], "revision": row["revision"],
            "event": row["event"], "progress": row["progress"], "provisional": bool(row["provisional"]),
            "replace_paths": json.loads(row["replace_paths_json"]), "data": json.loads(row["data_json"]),
            "created_at": row["created_at"],
        }


class DomainOnboardingJobManager:
    def __init__(self, pipeline: Any, store: JobStore, *, metrics: Any = None, audit_sink: Any = None, max_workers: int = 2):
        self.pipeline = pipeline
        self.store = store
        self.metrics = metrics
        self.audit_sink = audit_sink
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="domain-onboarding")
        self._contexts: dict[str, PipelineExecutionContext] = {}
        self._lock = Lock()
        self.store.recover_interrupted()

    def submit(self, request: DomainOnboardingRequest, *, client_request_id: str | None = None) -> dict[str, Any]:
        task_id = str(uuid4())
        dedupe_key = None
        if client_request_id:
            scope = f"{request.user_id or ''}:{request.session_id or ''}:{client_request_id}"
            dedupe_key = hashlib.sha256(scope.encode("utf-8")).hexdigest()
        job = self.store.create(task_id, request.model_dump(mode="json"), dedupe_key)
        if job["task_id"] != task_id:
            return job
        self.store.append_event(task_id, "accepted", 0.0, True, [], {"state": "queued"})
        self.executor.submit(self._run, task_id, request)
        return self.store.get(task_id) or job

    def _run(self, task_id: str, request: DomainOnboardingRequest) -> None:
        trace = DomainOnboardingRequestTrace(request_id=task_id)
        context = PipelineExecutionContext(timeout_seconds=self.pipeline.config.request_timeout_seconds)
        with self._lock:
            self._contexts[task_id] = context
        started = perf_counter()
        result: PipelineResult | None = None
        try:
            result = self.pipeline.run(request, trace, context, self._progress(task_id))
            trace.status = result.status
            response = result.to_response()
            if result.status == "cancelled":
                state, event = "cancelled", "cancelled"
            elif result.status in {"invalid_input", "planning_failed", "retrieval_failed", "generation_failed", "timeout", "internal_error"}:
                state, event = "failed", "failed"
            else:
                state, event = "completed", "completed"
            snapshot = self.store.get(task_id) or {}
            terminal_progress = 1.0 if state == "completed" else float(snapshot.get("progress", 0.0))
            self.store.append_event(task_id, event, terminal_progress, False, ["result"], {"result": response, "state": state})
            self.store.finish(task_id, state, response, result.error)
        except Exception as error:
            trace.status = "internal_error"
            snapshot = self.store.get(task_id) or {}
            self.store.append_event(
                task_id,
                "failed",
                float(snapshot.get("progress", 0.0)),
                False,
                [],
                {"error": str(error), "state": "failed"},
            )
            self.store.finish(task_id, "failed", None, str(error))
        finally:
            trace.total_duration_ms = round((perf_counter() - started) * 1000, 3)
            if self.audit_sink is not None:
                try:
                    self.audit_sink.write(build_audit_record(trace, query=request.query, session_id=request.session_id, user_id=request.user_id, result=result))
                except Exception:
                    trace.audit_write_failed = True
            if self.metrics is not None:
                self.metrics.record(trace)
            with self._lock:
                self._contexts.pop(task_id, None)

    def _progress(self, task_id: str):
        def callback(event: str, progress: float, provisional: bool, replace_paths: list[str], data: dict[str, Any]) -> None:
            self.store.append_event(task_id, event, progress, provisional, replace_paths, data)
        return callback

    def cancel(self, task_id: str) -> dict[str, Any] | None:
        job = self.store.request_cancel(task_id)
        if job and job["state"] == "cancel_requested":
            with self._lock:
                context = self._contexts.get(task_id)
            if context is not None:
                context.cancel()
        return job

    def close(self) -> None:
        with self._lock:
            contexts = list(self._contexts.values())
        for context in contexts:
            context.cancel()
        self.executor.shutdown(wait=True, cancel_futures=False)


def create_job_store_from_env() -> SQLiteJobStore:
    path = os.getenv(
        "DOMAIN_ONBOARDING_JOB_DB",
        "~/.novicesynapse/domain_onboarding_jobs.sqlite3",
    )
    return SQLiteJobStore(path)
