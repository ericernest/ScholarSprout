from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from fastapi.testclient import TestClient

from gateway.app import app
from handlers.domain_onboarding.jobs import (
    DomainOnboardingJobManager,
    JobQueueFullError,
    JobRateLimitError,
    SQLiteJobStore,
)
from handlers.domain_onboarding.schemas import DomainOnboardingRequest, PipelineResult


class FakePipeline:
    config = SimpleNamespace(request_timeout_seconds=5.0)

    def run(self, request, trace, execution_context=None, progress_callback=None):
        progress_callback("profile_ready", 0.2, True, ["learner_profile"], {"learner_profile": {"goal": "learn"}})
        progress_callback("development_ready", 0.6, True, ["development_stages"], {"development_stages": [{"stage_id": "stage_1"}]})
        return PipelineResult(status="ok", query=request.query)


class IncrementalJobTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = SQLiteJobStore(Path(self.directory.name) / "jobs.sqlite3")
        self.manager = DomainOnboardingJobManager(FakePipeline(), self.store, max_workers=1)

    def tearDown(self):
        self.manager.close()
        self.directory.cleanup()

    def test_store_replays_events_and_deduplicates_submission(self):
        request = DomainOnboardingRequest(query="RAG")
        first = self.manager.submit(request, client_request_id="same-request")
        second = self.manager.submit(request, client_request_id="same-request")
        self.assertEqual(first["task_id"], second["task_id"])
        for _ in range(100):
            snapshot = self.store.get(first["task_id"])
            if snapshot and snapshot["state"] == "completed":
                break
            time.sleep(0.01)
        self.assertEqual(snapshot["state"], "completed")
        events = self.store.events_after(first["task_id"], 0)
        self.assertEqual([item["event"] for item in events], ["accepted", "profile_ready", "development_ready", "completed"])
        self.assertNotIn("result", events[-1]["data"])
        self.assertTrue(events[-1]["data"]["result_available"])
        replay = self.store.events_after(first["task_id"], events[1]["id"])
        self.assertEqual([item["event"] for item in replay], ["development_ready", "completed"])

    def test_job_database_does_not_retain_internal_quality_payloads(self):
        task_id = "no-quality-persistence"
        self.store.create(task_id, {"query": "RAG"}, None)
        self.store.append_event(
            task_id,
            "quality_ready",
            0.9,
            True,
            ["quality"],
            {"quality": {"passed_hard_gates": False}, "domain": "RAG"},
        )
        self.store.finish(
            task_id,
            "completed",
            {
                "status": "ok",
                "domain": "RAG",
                "quality": {"score": 0.2},
                "quality_attempts": [{"attempt": 1}],
                "final_quality": {"verdict": "failed"},
                "repair_record": {"triggered": True},
            },
            None,
        )

        snapshot = self.store.get(task_id)
        events = self.store.events_after(task_id, 0)
        self.assertEqual(snapshot["result"], {"status": "ok", "domain": "RAG"})
        self.assertNotIn("quality", events[0]["data"])

        # Existing rows from older versions are scrubbed when the store opens.
        with self.store._connect() as db:
            db.execute(
                "UPDATE jobs SET result_json=? WHERE task_id=?",
                ('{"status":"ok","quality":{"score":0.1}}', task_id),
            )
        reopened = SQLiteJobStore(self.store.path)
        self.assertEqual(reopened.get(task_id)["result"], {"status": "ok"})

    def test_llm_deltas_are_batched_and_flushed_before_section_events(self):
        task_id = "batched-deltas"
        self.store.create(
            task_id,
            DomainOnboardingRequest(query="RAG").model_dump(mode="json"),
            None,
        )
        callback = self.manager._progress(task_id)

        for delta in ("one", "two", "three"):
            callback(
                "llm_delta",
                0.5,
                True,
                [],
                {"stage": "development", "delta": delta},
            )
        callback(
            "development_ready",
            0.68,
            True,
            ["development_stages"],
            {"development_stages": [{"stage_id": "stage_1"}]},
        )

        events = self.store.events_after(task_id, 0)
        self.assertEqual(
            [event["event"] for event in events],
            ["llm_delta", "development_ready"],
        )
        self.assertEqual(events[0]["data"]["delta"], "onetwothree")

    def test_gateway_supports_submit_poll_and_sse_replay(self):
        app.state.domain_onboarding_job_store = self.store
        app.state.domain_onboarding_job_manager = self.manager
        client = TestClient(app)
        created = client.post("/domain_onboarding/jobs", json={"query": "检索增强生成", "client_request_id": "api-test"})
        self.assertEqual(created.status_code, 202)
        task_id = created.json()["task_id"]
        token = created.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        self.assertEqual(
            client.get(f"/domain_onboarding/jobs/{task_id}").status_code,
            404,
        )
        for _ in range(100):
            snapshot = client.get(
                f"/domain_onboarding/jobs/{task_id}", headers=headers
            ).json()
            if snapshot["state"] == "completed":
                break
            time.sleep(0.01)
        self.assertEqual(snapshot["partial_result"], {})
        self.assertEqual(snapshot["result"]["status"], "ok")
        stream = client.get(
            f"/domain_onboarding/jobs/{task_id}/events", headers=headers
        )
        self.assertEqual(stream.status_code, 200)
        self.assertIn("event: development_ready", stream.text)
        self.assertIn("event: completed", stream.text)

    def test_restart_marks_unfinished_jobs_retryable_without_running_them(self):
        task_id = "unfinished"
        self.store.create(task_id, DomainOnboardingRequest(query="RAG").model_dump(mode="json"), None)
        recovered = self.store.recover_interrupted(stale_after_seconds=0)
        self.assertEqual(recovered, 1)
        snapshot = self.store.get(task_id)
        self.assertEqual(snapshot["state"], "interrupted")
        self.assertTrue(snapshot["retryable"])

    def test_failed_job_preserves_generated_partial_sections(self):
        class FailingPipeline:
            config = SimpleNamespace(request_timeout_seconds=5.0)

            def run(self, request, trace, execution_context=None, progress_callback=None):
                progress_callback(
                    "development_ready",
                    0.6,
                    True,
                    ["development_stages"],
                    {"development_stages": [{"stage_id": "stage_1"}]},
                )
                return PipelineResult(
                    status="generation_failed",
                    query=request.query,
                    error="learning_path section generation failed",
                )

        self.manager.close()
        self.manager = DomainOnboardingJobManager(FailingPipeline(), self.store, max_workers=1)
        job = self.manager.submit(DomainOnboardingRequest(query="RAG"))
        for _ in range(100):
            snapshot = self.store.get(job["task_id"])
            if snapshot and snapshot["state"] == "failed":
                break
            time.sleep(0.01)

        self.assertEqual(snapshot["state"], "failed")
        self.assertEqual(
            snapshot["partial_result"]["development_stages"],
            [{"stage_id": "stage_1"}],
        )
        self.assertEqual(snapshot["result"]["status"], "generation_failed")

    def test_cancel_is_cooperative_and_preserves_existing_events(self):
        started = Event()
        release = Event()

        class BlockingPipeline:
            config = SimpleNamespace(request_timeout_seconds=5.0)

            def run(self, request, trace, execution_context=None, progress_callback=None):
                progress_callback("profile_ready", 0.2, True, ["learner_profile"], {"learner_profile": {"goal": "learn"}})
                started.set()
                release.wait(timeout=2)
                return PipelineResult(
                    status="cancelled" if execution_context.cancel_event.is_set() else "ok",
                    query=request.query,
                )

        self.manager.close()
        self.manager = DomainOnboardingJobManager(BlockingPipeline(), self.store, max_workers=1)
        job = self.manager.submit(DomainOnboardingRequest(query="RAG"))
        self.assertTrue(started.wait(timeout=1))
        cancelled = self.manager.cancel(job["task_id"])
        self.assertEqual(cancelled["state"], "cancel_requested")
        release.set()
        for _ in range(100):
            snapshot = self.store.get(job["task_id"])
            if snapshot["state"] == "cancelled":
                break
            time.sleep(0.01)
        self.assertEqual(snapshot["state"], "cancelled")
        self.assertIn("learner_profile", snapshot["partial_result"])
        events = self.store.events_after(job["task_id"], 0)
        self.assertIn("cancel_requested", [event["event"] for event in events])
        self.assertEqual(events[-1]["event"], "cancelled")

    def test_access_tokens_are_stable_across_manager_restart(self):
        job = self.manager.submit(DomainOnboardingRequest(query="RAG"))
        token = job["access_token"]
        self.assertTrue(self.manager.authorize(job["task_id"], token))
        for _ in range(100):
            if self.store.get(job["task_id"])["state"] == "completed":
                break
            time.sleep(0.01)
        self.manager.close()
        self.manager = DomainOnboardingJobManager(FakePipeline(), self.store, max_workers=1)
        self.assertTrue(self.manager.authorize(job["task_id"], token))
        self.assertFalse(self.manager.authorize(job["task_id"], "wrong"))

    def test_global_queue_and_owner_rate_limits_are_enforced(self):
        started = Event()
        release = Event()

        class BlockingPipeline:
            config = SimpleNamespace(request_timeout_seconds=5.0)

            def run(self, request, trace, execution_context=None, progress_callback=None):
                started.set()
                release.wait(timeout=2)
                return PipelineResult(status="ok", query=request.query)

        self.manager.close()
        self.manager = DomainOnboardingJobManager(
            BlockingPipeline(),
            self.store,
            max_workers=1,
            max_queue_size=0,
            per_owner_active_limit=2,
        )
        self.manager.submit(DomainOnboardingRequest(query="RAG"), owner_key="one")
        self.assertTrue(started.wait(timeout=1))
        with self.assertRaises(JobQueueFullError):
            self.manager.submit(DomainOnboardingRequest(query="GNN"), owner_key="two")
        release.set()

        self.manager.close()
        self.manager = DomainOnboardingJobManager(
            FakePipeline(), self.store, submissions_per_minute=1
        )
        self.manager.submit(DomainOnboardingRequest(query="RAG"), owner_key="rate")
        with self.assertRaises(JobRateLimitError):
            self.manager.submit(DomainOnboardingRequest(query="GNN"), owner_key="rate")

    def test_retry_creates_a_linked_task_and_cleanup_removes_expired_rows(self):
        class FailedPipeline:
            config = SimpleNamespace(request_timeout_seconds=5.0)

            def run(self, request, trace, execution_context=None, progress_callback=None):
                return PipelineResult(status="generation_failed", query=request.query)

        self.manager.close()
        self.manager = DomainOnboardingJobManager(FailedPipeline(), self.store)
        first = self.manager.submit(DomainOnboardingRequest(query="RAG"))
        for _ in range(100):
            snapshot = self.store.get(first["task_id"])
            if snapshot["state"] == "failed":
                break
            time.sleep(0.01)
        retried = self.manager.retry(first["task_id"])
        self.assertNotEqual(first["task_id"], retried["task_id"])
        self.assertEqual(retried["parent_task_id"], first["task_id"])
        for _ in range(100):
            if self.store.get(retried["task_id"])["state"] == "failed":
                break
            time.sleep(0.01)
        with self.store._connect() as db:
            db.execute(
                "UPDATE jobs SET updated_at='2000-01-01 00:00:00' WHERE task_id IN (?,?)",
                (first["task_id"], retried["task_id"]),
            )
        self.assertEqual(self.store.purge_expired(60), 2)
        self.assertIsNone(self.store.get(first["task_id"]))

    def test_close_cancels_running_and_queued_jobs(self):
        started = Event()

        class ShutdownAwarePipeline:
            config = SimpleNamespace(request_timeout_seconds=5.0)

            def run(self, request, trace, execution_context=None, progress_callback=None):
                if request.query == "first":
                    started.set()
                    execution_context.cancel_event.wait(timeout=2)
                return PipelineResult(
                    status=(
                        "cancelled"
                        if execution_context.cancel_event.is_set()
                        else "ok"
                    ),
                    query=request.query,
                )

        self.manager.close()
        self.manager = DomainOnboardingJobManager(
            ShutdownAwarePipeline(),
            self.store,
            max_workers=1,
            max_queue_size=1,
            per_owner_active_limit=2,
        )
        first = self.manager.submit(DomainOnboardingRequest(query="first"))
        self.assertTrue(started.wait(timeout=1))
        second = self.manager.submit(DomainOnboardingRequest(query="second"))

        self.manager.close()

        self.assertEqual(self.store.get(first["task_id"])["state"], "cancelled")
        self.assertEqual(self.store.get(second["task_id"])["state"], "cancelled")

    def test_metrics_failures_do_not_change_job_result_or_leak_capacity(self):
        class BrokenMetrics:
            def record_job_event(self, event, count=1):
                raise RuntimeError("metrics unavailable")

            def record(self, trace):
                raise RuntimeError("metrics unavailable")

        self.manager.close()
        self.manager = DomainOnboardingJobManager(
            FakePipeline(),
            self.store,
            metrics=BrokenMetrics(),
            max_workers=1,
            max_queue_size=0,
        )
        first = self.manager.submit(DomainOnboardingRequest(query="RAG"))
        for _ in range(100):
            if self.store.get(first["task_id"])["state"] == "completed":
                break
            time.sleep(0.01)
        self.assertEqual(self.store.get(first["task_id"])["state"], "completed")

        second = self.manager.submit(DomainOnboardingRequest(query="GNN"))
        for _ in range(100):
            if self.store.get(second["task_id"])["state"] == "completed":
                break
            time.sleep(0.01)
        self.assertEqual(self.store.get(second["task_id"])["state"], "completed")


if __name__ == "__main__":
    unittest.main()
