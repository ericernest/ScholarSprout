"""Persistent, access-controlled asynchronous jobs and replayable events."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import BoundedSemaphore, Lock
from time import monotonic, perf_counter
from typing import Any, Iterator, Protocol
from uuid import uuid4

from .audit import build_audit_record
from .execution import PipelineExecutionContext
from .metrics import DomainOnboardingRequestTrace
from .schemas import DomainOnboardingRequest, PipelineResult


TERMINAL_STATES = {"completed", "failed", "cancelled", "interrupted"}
RETRYABLE_STATES = {"failed", "cancelled", "interrupted"}


class JobQueueFullError(RuntimeError):
    """The bounded global job capacity has been exhausted."""


class JobRateLimitError(RuntimeError):
    """An owner exceeded its submission or active-job limit."""


class JobNotRetryableError(RuntimeError):
    """A retry was requested for a non-retryable job."""


class JobStore(Protocol):
    def create(
        self,
        task_id: str,
        request: dict[str, Any],
        client_request_id: str | None,
        owner_scope: str,
        parent_task_id: str | None = None,
    ) -> dict[str, Any]: ...
    def get(self, task_id: str) -> dict[str, Any] | None: ...
    def get_by_client_request_id(self, client_request_id: str) -> dict[str, Any] | None: ...
    def count_active(self, owner_scope: str) -> int: ...
    def append_event(self, task_id: str, event: str, progress: float, provisional: bool, replace_paths: list[str], data: dict[str, Any]) -> dict[str, Any]: ...
    def finish(self, task_id: str, state: str, result: dict[str, Any] | None, error: str | None) -> None: ...
    def finish_with_event(self, task_id: str, state: str, result: dict[str, Any] | None, error: str | None, event: str, progress: float) -> None: ...
    def request_cancel(self, task_id: str) -> dict[str, Any] | None: ...
    def events_after(self, task_id: str, event_id: int) -> list[dict[str, Any]]: ...
    def recover_interrupted(self, stale_after_seconds: int = 0) -> int: ...
    def purge_expired(self, retention_seconds: int, limit: int = 500) -> int: ...
    def get_or_create_secret(self, name: str) -> bytes: ...


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
        connection.execute("PRAGMA foreign_keys=ON")
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
                owner_scope TEXT NOT NULL DEFAULT 'anonymous', parent_task_id TEXT,
                state TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
                current_stage TEXT NOT NULL DEFAULT 'accepted', progress REAL NOT NULL DEFAULT 0,
                request_json TEXT NOT NULL, partial_json TEXT NOT NULL DEFAULT '{}',
                result_json TEXT, error TEXT, retryable INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"""
            )
            self._ensure_column(db, "jobs", "owner_scope", "TEXT NOT NULL DEFAULT 'anonymous'")
            self._ensure_column(db, "jobs", "parent_task_id", "TEXT")
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS jobs_client_request_id ON jobs(client_request_id) WHERE client_request_id IS NOT NULL"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS jobs_owner_state ON jobs(owner_scope,state)"
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS job_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                revision INTEGER NOT NULL, event TEXT NOT NULL, progress REAL NOT NULL,
                provisional INTEGER NOT NULL, replace_paths_json TEXT NOT NULL,
                data_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(task_id) REFERENCES jobs(task_id) ON DELETE CASCADE)"""
            )
            db.execute("CREATE INDEX IF NOT EXISTS job_events_task_id_id ON job_events(task_id, id)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS job_metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )

    @staticmethod
    def _ensure_column(
        db: sqlite3.Connection, table: str, column: str, definition: str
    ) -> None:
        columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def create(
        self,
        task_id: str,
        request: dict[str, Any],
        client_request_id: str | None,
        owner_scope: str = "anonymous",
        parent_task_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            with self._lock, self._connect() as db:
                if client_request_id:
                    existing = db.execute(
                        "SELECT * FROM jobs WHERE client_request_id=?",
                        (client_request_id,),
                    ).fetchone()
                    if existing:
                        return self._row(existing)
                db.execute(
                    "INSERT INTO jobs(task_id,client_request_id,owner_scope,parent_task_id,state,request_json) VALUES(?,?,?,?,?,?)",
                    (
                        task_id,
                        client_request_id,
                        owner_scope,
                        parent_task_id,
                        "queued",
                        json.dumps(request, ensure_ascii=False),
                    ),
                )
        except sqlite3.IntegrityError:
            if client_request_id:
                existing = self.get_by_client_request_id(client_request_id)
                if existing is not None:
                    return existing
            raise
        return self.get(task_id) or {}

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE task_id=?", (task_id,)).fetchone()
        return self._row(row) if row else None

    def get_by_client_request_id(self, client_request_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM jobs WHERE client_request_id=?", (client_request_id,)
            ).fetchone()
        return self._row(row) if row else None

    def count_active(self, owner_scope: str) -> int:
        placeholders = ",".join("?" for _ in ("queued", "running", "cancel_requested"))
        with self._connect() as db:
            row = db.execute(
                f"SELECT COUNT(*) AS count FROM jobs WHERE owner_scope=? AND state IN ({placeholders})",
                (owner_scope, "queued", "running", "cancel_requested"),
            ).fetchone()
        return int(row["count"] if row else 0)

    def append_event(
        self,
        task_id: str,
        event: str,
        progress: float,
        provisional: bool,
        replace_paths: list[str],
        data: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT revision,partial_json,state FROM jobs WHERE task_id=?", (task_id,)
            ).fetchone()
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
                (
                    task_id,
                    revision,
                    event,
                    progress,
                    int(provisional),
                    json.dumps(replace_paths),
                    json.dumps(data, ensure_ascii=False),
                ),
            )
            state = row["state"]
            if state not in TERMINAL_STATES and state != "cancel_requested":
                state = "running" if event != "accepted" else "queued"
            db.execute(
                "UPDATE jobs SET state=?,revision=?,current_stage=?,progress=?,partial_json=?,updated_at=CURRENT_TIMESTAMP WHERE task_id=?",
                (
                    state,
                    revision,
                    event,
                    progress,
                    json.dumps(partial, ensure_ascii=False),
                    task_id,
                ),
            )
            event_id = int(cursor.lastrowid)
        return {
            "id": event_id,
            "task_id": task_id,
            "revision": revision,
            "event": event,
            "progress": progress,
            "provisional": provisional,
            "replace_paths": replace_paths,
            "data": data,
        }

    def finish(
        self,
        task_id: str,
        state: str,
        result: dict[str, Any] | None,
        error: str | None,
    ) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "UPDATE jobs SET state=?,progress=CASE WHEN ?='completed' THEN 1.0 ELSE progress END,result_json=?,error=?,retryable=?,partial_json=CASE WHEN ?='completed' THEN '{}' ELSE partial_json END,updated_at=CURRENT_TIMESTAMP WHERE task_id=?",
                (
                    state,
                    state,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    error,
                    int(state in RETRYABLE_STATES),
                    state,
                    task_id,
                ),
            )

    def finish_with_event(
        self,
        task_id: str,
        state: str,
        result: dict[str, Any] | None,
        error: str | None,
        event: str,
        progress: float,
    ) -> None:
        """Commit terminal state and its replay event in one transaction."""
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT revision FROM jobs WHERE task_id=?", (task_id,)
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            revision = int(row["revision"]) + 1
            db.execute(
                """UPDATE jobs SET state=?,revision=?,current_stage=?,progress=?,result_json=?,error=?,retryable=?,
                   partial_json=CASE WHEN ?='completed' THEN '{}' ELSE partial_json END,updated_at=CURRENT_TIMESTAMP
                   WHERE task_id=?""",
                (
                    state,
                    revision,
                    event,
                    progress,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    error,
                    int(state in RETRYABLE_STATES),
                    state,
                    task_id,
                ),
            )
            db.execute(
                """INSERT INTO job_events(task_id,revision,event,progress,provisional,replace_paths_json,data_json)
                   VALUES(?,?,?,?,0,?,?)""",
                (
                    task_id,
                    revision,
                    event,
                    progress,
                    json.dumps(["result"] if result is not None else []),
                    json.dumps(
                        {"state": state, "result_available": result is not None},
                        ensure_ascii=False,
                    ),
                ),
            )

    def request_cancel(self, task_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT state FROM jobs WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                return None
            if row["state"] not in TERMINAL_STATES and row["state"] != "cancel_requested":
                db.execute(
                    "UPDATE jobs SET state='cancel_requested',updated_at=CURRENT_TIMESTAMP WHERE task_id=?",
                    (task_id,),
                )
        return self.get(task_id)

    def events_after(self, task_id: str, event_id: int) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM job_events WHERE task_id=? AND id>? ORDER BY id",
                (task_id, event_id),
            ).fetchall()
        return [self._event_row(row) for row in rows]

    def recover_interrupted(self, stale_after_seconds: int = 0) -> int:
        modifier = f"-{max(0, int(stale_after_seconds))} seconds"
        with self._lock, self._connect() as db:
            cursor = db.execute(
                """UPDATE jobs
                SET state='interrupted',error='Gateway stopped before completion.',retryable=1,updated_at=CURRENT_TIMESTAMP
                WHERE state IN ('queued','running','cancel_requested')
                AND updated_at <= datetime('now', ?)""",
                (modifier,),
            )
            return cursor.rowcount

    def purge_expired(self, retention_seconds: int, limit: int = 500) -> int:
        modifier = f"-{max(1, int(retention_seconds))} seconds"
        with self._lock, self._connect() as db:
            rows = db.execute(
                """SELECT task_id FROM jobs
                WHERE state IN ('completed','failed','cancelled','interrupted')
                AND updated_at <= datetime('now', ?)
                ORDER BY updated_at LIMIT ?""",
                (modifier, max(1, int(limit))),
            ).fetchall()
            task_ids = [row["task_id"] for row in rows]
            if not task_ids:
                return 0
            placeholders = ",".join("?" for _ in task_ids)
            db.execute(f"DELETE FROM job_events WHERE task_id IN ({placeholders})", task_ids)
            db.execute(f"DELETE FROM jobs WHERE task_id IN ({placeholders})", task_ids)
            return len(task_ids)

    def get_or_create_secret(self, name: str) -> bytes:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT value FROM job_metadata WHERE name=?", (name,)
            ).fetchone()
            if row is None:
                value = secrets.token_urlsafe(48)
                db.execute(
                    "INSERT INTO job_metadata(name,value) VALUES(?,?)", (name, value)
                )
            else:
                value = str(row["value"])
        return value.encode("utf-8")

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "task_id": row["task_id"],
            "parent_task_id": row["parent_task_id"],
            "state": row["state"],
            "revision": row["revision"],
            "current_stage": row["current_stage"],
            "progress": row["progress"],
            "request": json.loads(row["request_json"]),
            "partial_result": json.loads(row["partial_json"] or "{}"),
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "error": row["error"],
            "retryable": bool(row["retryable"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _event_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "task_id": row["task_id"],
            "revision": row["revision"],
            "event": row["event"],
            "progress": row["progress"],
            "provisional": bool(row["provisional"]),
            "replace_paths": json.loads(row["replace_paths_json"]),
            "data": json.loads(row["data_json"]),
            "created_at": row["created_at"],
        }


class DomainOnboardingJobManager:
    def __init__(
        self,
        pipeline: Any,
        store: JobStore,
        *,
        metrics: Any = None,
        audit_sink: Any = None,
        max_workers: int = 2,
        max_queue_size: int = 20,
        per_owner_active_limit: int = 2,
        submissions_per_minute: int = 10,
        retention_seconds: int = 7 * 24 * 60 * 60,
        recovery_stale_seconds: int = 15 * 60,
        token_secret: str | bytes | None = None,
        result_store: Any = None,
        memory_service: Any = None,
    ):
        self.pipeline = pipeline
        self.store = store
        self.metrics = metrics
        self.audit_sink = audit_sink
        self.result_store = result_store
        self.memory_service = memory_service
        self.max_workers = max(1, int(max_workers))
        self.max_queue_size = max(0, int(max_queue_size))
        self.per_owner_active_limit = max(1, int(per_owner_active_limit))
        self.submissions_per_minute = max(1, int(submissions_per_minute))
        self.retention_seconds = max(60, int(retention_seconds))
        self.recovery_stale_seconds = max(0, int(recovery_stale_seconds))
        self.executor = ThreadPoolExecutor(
            max_workers=self.max_workers, thread_name_prefix="domain-onboarding"
        )
        self._capacity = BoundedSemaphore(self.max_workers + self.max_queue_size)
        self._contexts: dict[str, PipelineExecutionContext] = {}
        self._submitted_task_ids: set[str] = set()
        self._submission_times: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()
        self._submission_lock = Lock()
        configured_secret = token_secret or os.getenv("DOMAIN_ONBOARDING_JOB_TOKEN_SECRET")
        if isinstance(configured_secret, str):
            configured_secret = configured_secret.encode("utf-8")
        self._token_secret = configured_secret or self.store.get_or_create_secret(
            "job_token_secret_v1"
        )
        purged = self.store.purge_expired(self.retention_seconds)
        self._record_job_metric("expired_purged", purged)
        self.store.recover_interrupted(self.recovery_stale_seconds)

    def submit(
        self,
        request: DomainOnboardingRequest,
        *,
        client_request_id: str | None = None,
        owner_key: str = "anonymous",
        parent_task_id: str | None = None,
    ) -> dict[str, Any]:
        owner_scope = hashlib.sha256(owner_key.encode("utf-8")).hexdigest()
        dedupe_key = None
        if client_request_id:
            scope = f"{owner_scope}:{client_request_id}"
            dedupe_key = hashlib.sha256(scope.encode("utf-8")).hexdigest()
            existing = self.store.get_by_client_request_id(dedupe_key)
            if existing is not None:
                self._record_job_metric("deduplicated")
                return self._with_access_token(existing)

        with self._submission_lock:
            purged = self.store.purge_expired(self.retention_seconds)
            self._record_job_metric("expired_purged", purged)
            recovered = self.store.recover_interrupted(self.recovery_stale_seconds)
            self._record_job_metric("interrupted_recovered", recovered)
            try:
                self._enforce_rate_limit(owner_scope)
            except JobRateLimitError:
                self._record_job_metric("rate_rejected")
                raise
            if self.store.count_active(owner_scope) >= self.per_owner_active_limit:
                self._record_job_metric("owner_active_rejected")
                raise JobRateLimitError(
                    "Too many active domain onboarding jobs for this requester."
                )
            if not self._capacity.acquire(blocking=False):
                self._record_job_metric("queue_rejected")
                raise JobQueueFullError("Domain onboarding job queue is full.")
            task_id = str(uuid4())
            try:
                job = self.store.create(
                    task_id,
                    request.model_dump(mode="json"),
                    dedupe_key,
                    owner_scope,
                    parent_task_id,
                )
                if job["task_id"] != task_id:
                    self._capacity.release()
                    self._record_job_metric("deduplicated")
                    return self._with_access_token(job)
                self.store.append_event(
                    task_id, "accepted", 0.0, True, [], {"state": "queued"}
                )
                current_message_id = self._persist_submission(task_id, request)
                if current_message_id:
                    request.metadata["_memory_current_message_id"] = current_message_id
                with self._lock:
                    self._submitted_task_ids.add(task_id)
                self.executor.submit(self._run, task_id, request)
                self._record_job_metric("submitted")
            except Exception:
                with self._lock:
                    self._submitted_task_ids.discard(task_id)
                self._capacity.release()
                raise
        return self._with_access_token(self.store.get(task_id) or job)

    def retry(self, task_id: str, *, owner_key: str = "anonymous") -> dict[str, Any]:
        previous = self.store.get(task_id)
        if previous is None:
            raise KeyError(task_id)
        if previous["state"] not in RETRYABLE_STATES:
            raise JobNotRetryableError(
                f"Job in state {previous['state']} cannot be retried."
            )
        request = DomainOnboardingRequest.model_validate(previous["request"])
        retried = self.submit(request, owner_key=owner_key, parent_task_id=task_id)
        self._record_job_metric("retried")
        return retried

    def access_token(self, task_id: str) -> str:
        digest = hmac.new(
            self._token_secret, task_id.encode("utf-8"), hashlib.sha256
        ).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    def authorize(self, task_id: str, token: str | None) -> bool:
        return bool(token) and hmac.compare_digest(
            self.access_token(task_id), str(token)
        )

    def _with_access_token(self, job: dict[str, Any]) -> dict[str, Any]:
        return {**job, "access_token": self.access_token(job["task_id"])}

    def _enforce_rate_limit(self, owner_scope: str) -> None:
        now = monotonic()
        submissions = self._submission_times[owner_scope]
        while submissions and submissions[0] <= now - 60:
            submissions.popleft()
        if len(submissions) >= self.submissions_per_minute:
            raise JobRateLimitError(
                "Domain onboarding submission rate limit exceeded."
            )
        submissions.append(now)

    def _run(self, task_id: str, request: DomainOnboardingRequest) -> None:
        trace = DomainOnboardingRequestTrace(request_id=task_id)
        context = PipelineExecutionContext(
            timeout_seconds=self.pipeline.config.request_timeout_seconds
        )
        with self._lock:
            self._contexts[task_id] = context
        snapshot_before_run = self.store.get(task_id)
        if snapshot_before_run and snapshot_before_run["state"] == "cancel_requested":
            context.cancel()
        started = perf_counter()
        result: PipelineResult | None = None
        progress_callback = self._progress(task_id)
        try:
            if self.memory_service is not None and request.session_id:
                try:
                    conversation_context = self.memory_service.prepare_context(
                        request.session_id,
                        exclude_message_id=str(
                            request.metadata.get("_memory_current_message_id") or ""
                        )
                        or None,
                    )
                    request.metadata["conversation_context"] = {
                        "long_term_memory": conversation_context.memory_text,
                        "recent_messages": conversation_context.context_messages,
                    }
                except Exception:
                    # Memory is optional context; onboarding must continue when
                    # compression or storage is temporarily unavailable.
                    request.metadata["conversation_context"] = {}
            result = self.pipeline.run(request, trace, context, progress_callback)
            progress_callback.flush()  # type: ignore[attr-defined]
            trace.status = result.status
            response = result.to_response()
            if result.status == "cancelled":
                state, event = "cancelled", "cancelled"
            elif result.status in {
                "invalid_input",
                "planning_failed",
                "retrieval_failed",
                "generation_failed",
                "timeout",
                "internal_error",
            }:
                state, event = "failed", "failed"
            else:
                state, event = "completed", "completed"
            snapshot = self.store.get(task_id) or {}
            terminal_progress = (
                1.0 if state == "completed" else float(snapshot.get("progress", 0.0))
            )
            self._persist_result(task_id, request, response)
            self._finish_job(
                task_id,
                state=state,
                result=response,
                error=result.error,
                event=event,
                progress=terminal_progress,
            )
            self._record_job_metric(state)
        except Exception as error:
            progress_callback.flush()  # type: ignore[attr-defined]
            trace.status = "internal_error"
            snapshot = self.store.get(task_id) or {}
            self._persist_failure(task_id, str(error))
            self._finish_job(
                task_id,
                state="failed",
                result=None,
                error=str(error),
                event="failed",
                progress=float(snapshot.get("progress", 0.0)),
            )
            self._record_job_metric("failed")
        finally:
            progress_callback.flush()  # type: ignore[attr-defined]
            trace.total_duration_ms = round((perf_counter() - started) * 1000, 3)
            if self.audit_sink is not None:
                try:
                    self.audit_sink.write(
                        build_audit_record(
                            trace,
                            query=request.query,
                            session_id=request.session_id,
                            user_id=request.user_id,
                            result=result,
                        )
                    )
                except Exception:
                    trace.audit_write_failed = True
            if self.metrics is not None:
                try:
                    self.metrics.record(trace)
                except Exception:
                    # Metrics failure must not leak worker capacity or alter a
                    # terminal result that has already been persisted.
                    pass
            with self._lock:
                self._contexts.pop(task_id, None)
                self._submitted_task_ids.discard(task_id)
            self._capacity.release()

    def _progress(self, task_id: str):
        delta_lock = Lock()
        pending: dict[str, dict[str, Any]] = {}

        def persist(
            event: str,
            progress: float,
            provisional: bool,
            replace_paths: list[str],
            data: dict[str, Any],
        ) -> None:
            self.store.append_event(
                task_id, event, progress, provisional, replace_paths, data
            )
            if self.result_store is not None:
                try:
                    self.result_store.update_domain_onboarding_state(
                        task_id,
                        state="running",
                        current_stage=event,
                    )
                except Exception:
                    pass

        def flush(stage: str | None = None) -> None:
            with delta_lock:
                stages = [stage] if stage is not None else list(pending)
                batches = [pending.pop(name) for name in stages if name in pending]
            for batch in batches:
                persist(
                    "llm_delta",
                    batch["progress"],
                    True,
                    [],
                    {"stage": batch["stage"], "delta": batch["delta"]},
                )

        def callback(
            event: str,
            progress: float,
            provisional: bool,
            replace_paths: list[str],
            data: dict[str, Any],
        ) -> None:
            if event != "llm_delta":
                flush()
                persist(event, progress, provisional, replace_paths, data)
                return

            stage = str(data.get("stage") or "generation")
            delta = str(data.get("delta") or "")
            if not delta:
                return
            now = monotonic()
            should_flush = False
            with delta_lock:
                batch = pending.setdefault(
                    stage,
                    {
                        "stage": stage,
                        "delta": "",
                        "progress": progress,
                        "started_at": now,
                    },
                )
                batch["delta"] += delta
                batch["progress"] = progress
                should_flush = (
                    len(batch["delta"]) >= 512
                    or now - float(batch["started_at"]) >= 0.5
                )
            if should_flush:
                flush(stage)

        callback.flush = flush  # type: ignore[attr-defined]
        return callback

    def _persist_submission(self, task_id: str, request: DomainOnboardingRequest) -> str | None:
        if self.result_store is None:
            return None
        try:
            current_message_id: str | None = None
            if request.session_id:
                self.result_store.ensure_conversation(
                    request.session_id,
                    title=request.query[:60] or "新会话",
                    user_id=request.user_id,
                )
                current_message_id = self.result_store.append_message(
                    request.session_id,
                    role="user",
                    content=request.query,
                    mode="domain_onboarding",
                    channel="web",
                )
            self.result_store.create_domain_onboarding(
                artifact_id=task_id,
                title=f"领域入门：{request.query[:80]}",
                query=request.query,
                language=request.language,
                current_stage="queued",
                conversation_id=request.session_id,
            )
            return current_message_id
        except Exception:
            # Product persistence cannot alter job admission or execution.
            return None

    def _persist_result(
        self,
        task_id: str,
        request: DomainOnboardingRequest,
        response: dict[str, Any],
    ) -> None:
        if self.result_store is None:
            return
        try:
            self.result_store.persist_domain_onboarding_result(
                artifact_id=task_id,
                query=request.query,
                response=response,
                conversation_id=request.session_id,
                user_id=request.user_id,
            )
            if request.session_id:
                text = str(response.get("text") or "领域入门任务已完成。")
                self.result_store.append_message(
                    request.session_id,
                    role="assistant",
                    content=text,
                    mode="domain_onboarding",
                    channel="web",
                )
        except Exception:
            return

    def _persist_failure(self, task_id: str, error: str) -> None:
        if self.result_store is None:
            return
        try:
            self.result_store.update_domain_onboarding_state(
                task_id,
                state="failed",
                current_stage="internal_error",
                error_summary=error,
            )
        except Exception:
            return

    def _finish_job(
        self,
        task_id: str,
        *,
        state: str,
        result: dict[str, Any] | None,
        error: str | None,
        event: str,
        progress: float,
    ) -> None:
        atomic_finish = getattr(self.store, "finish_with_event", None)
        if callable(atomic_finish):
            atomic_finish(task_id, state, result, error, event, progress)
            return
        self.store.finish(task_id, state, result, error)
        self.store.append_event(
            task_id,
            event,
            progress,
            False,
            ["result"] if result is not None else [],
            {"state": state, "result_available": result is not None},
        )

    def cancel(self, task_id: str) -> dict[str, Any] | None:
        before = self.store.get(task_id)
        job = self.store.request_cancel(task_id)
        if (
            before is not None
            and before["state"] != "cancel_requested"
            and job is not None
            and job["state"] == "cancel_requested"
        ):
            self.store.append_event(
                task_id,
                "cancel_requested",
                float(job.get("progress", 0.0)),
                True,
                [],
                {
                    "state": "cancel_requested",
                    "message": "Cancellation will complete at the next pipeline boundary.",
                },
            )
            with self._lock:
                context = self._contexts.get(task_id)
            if context is not None:
                context.cancel()
            self._record_job_metric("cancel_requested")
        return self.store.get(task_id) if job is not None else None

    def _record_job_metric(self, event: str, count: int = 1) -> None:
        if not isinstance(count, int) or count <= 0 or self.metrics is None:
            return
        recorder = getattr(self.metrics, "record_job_event", None)
        if callable(recorder):
            try:
                recorder(event, count)
            except Exception:
                # Observability must never change job admission or execution.
                return

    def close(self) -> None:
        with self._lock:
            task_ids = list(self._submitted_task_ids)
            contexts = list(self._contexts.values())
        # Mark every submitted job before waking any running worker.  Cancelling
        # the active context first can let the executor start and finish a queued
        # job before that job is marked as cancelled.
        for task_id in task_ids:
            before = self.store.get(task_id)
            job = self.store.request_cancel(task_id)
            if (
                before is not None
                and before["state"] != "cancel_requested"
                and job is not None
                and job["state"] == "cancel_requested"
            ):
                self.store.append_event(
                    task_id,
                    "cancel_requested",
                    float(job.get("progress", 0.0)),
                    True,
                    [],
                    {
                        "state": "cancel_requested",
                        "message": "Gateway shutdown requested cancellation.",
                    },
                )
                self._record_job_metric("cancel_requested")
        for context in contexts:
            context.cancel()
        self.executor.shutdown(wait=True, cancel_futures=False)


def create_job_store_from_env(default_path: str | Path | None = None) -> SQLiteJobStore:
    path = os.getenv("DOMAIN_ONBOARDING_JOB_DB")
    if not path:
        path = str(default_path or "~/.novicesynapse/research.sqlite3")
    return SQLiteJobStore(path)
