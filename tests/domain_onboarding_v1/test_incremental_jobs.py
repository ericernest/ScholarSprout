from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from fastapi.testclient import TestClient

from gateway.app import app
from handlers.domain_onboarding.jobs import DomainOnboardingJobManager, SQLiteJobStore
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
        replay = self.store.events_after(first["task_id"], events[1]["id"])
        self.assertEqual([item["event"] for item in replay], ["development_ready", "completed"])

    def test_gateway_supports_submit_poll_and_sse_replay(self):
        app.state.domain_onboarding_job_store = self.store
        app.state.domain_onboarding_job_manager = self.manager
        client = TestClient(app)
        created = client.post("/domain_onboarding/jobs", json={"query": "检索增强生成", "client_request_id": "api-test"})
        self.assertEqual(created.status_code, 202)
        task_id = created.json()["task_id"]
        for _ in range(100):
            snapshot = client.get(f"/domain_onboarding/jobs/{task_id}").json()
            if snapshot["state"] == "completed":
                break
            time.sleep(0.01)
        self.assertEqual(snapshot["partial_result"], {})
        self.assertEqual(snapshot["result"]["status"], "ok")
        stream = client.get(f"/domain_onboarding/jobs/{task_id}/events")
        self.assertEqual(stream.status_code, 200)
        self.assertIn("event: development_ready", stream.text)
        self.assertIn("event: completed", stream.text)

    def test_restart_marks_unfinished_jobs_retryable_without_running_them(self):
        task_id = "unfinished"
        self.store.create(task_id, DomainOnboardingRequest(query="RAG").model_dump(mode="json"), None)
        recovered = self.store.recover_interrupted()
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
        self.assertEqual(self.store.events_after(job["task_id"], 0)[-1]["event"], "cancelled")


if __name__ == "__main__":
    unittest.main()
