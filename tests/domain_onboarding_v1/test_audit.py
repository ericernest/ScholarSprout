from __future__ import annotations

import json
import stat
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from channels.base import ChannelMessage
from handlers.domain_onboarding.audit import (
    DomainOnboardingAuditRecord,
    JsonlAuditSink,
    build_audit_record,
)
from handlers.domain_onboarding.metrics import (
    DomainOnboardingMetrics,
    DomainOnboardingRequestTrace,
)
from handlers.domain_onboarding.schemas import (
    ContentQuality,
    PipelineResult,
    QualityAttempt,
    RepairActionRecord,
    RepairRecord,
)
from handlers.domain_onboarding_handler import handle_domain_onboarding_message


class AuditTests(unittest.TestCase):
    def test_jsonl_sink_persists_one_redacted_record_per_line(self) -> None:
        trace = DomainOnboardingRequestTrace(
            status="quality_warning",
            total_duration_ms=12.5,
            first_model_calls=1,
            first_quality_state="warning",
            policy_fingerprint="0123456789abcdef",
        )
        quality = ContentQuality(
            score=0.7,
            threshold=0.75,
            passed_hard_gates=True,
            dimensions={"structure": 0.8},
            policy_fingerprint=trace.policy_fingerprint,
        )
        result = PipelineResult(
            policy_fingerprint=trace.policy_fingerprint,
            status="quality_warning",
            query="private query",
            quality=quality,
            quality_attempts=[
                QualityAttempt(attempt_number=1, source="initial", quality=quality)
            ],
            repair_record=RepairRecord(
                triggered=True,
                policy_fingerprint=trace.policy_fingerprint,
                actions=[
                    RepairActionRecord(
                        action_id="repair-1",
                        action_type="llm",
                        status="failed",
                        error="secret upstream failure body",
                    )
                ],
            ),
        )
        record = build_audit_record(
            trace,
            query="private query",
            session_id="private session",
            user_id="private user",
            result=result,
        )

        with tempfile.TemporaryDirectory() as directory:
            sink = JsonlAuditSink(directory, fsync=True)
            sink.write(record)
            path = next(Path(directory).glob("domain-onboarding-*.jsonl"))
            raw = path.read_text(encoding="utf-8")
            stored = json.loads(raw)

            self.assertEqual(stored["request_id"], trace.request_id)
            self.assertEqual(stored["status"], "quality_warning")
            self.assertEqual(len(stored["quality_attempts"]), 1)
            self.assertNotIn("private query", raw)
            self.assertNotIn("private session", raw)
            self.assertNotIn("private user", raw)
            self.assertNotIn("secret upstream failure body", raw)
            self.assertIsNone(stored["repair_record"]["actions"][0]["error"])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_jsonl_sink_appends_complete_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sink = JsonlAuditSink(directory)
            for index in range(2):
                sink.write(
                    DomainOnboardingAuditRecord(
                        request_id=f"request-{index}",
                        recorded_at=datetime(2026, 7, 27, tzinfo=UTC),
                        query_hash=f"hash-{index}",
                        policy_version="domain-quality-v1.0.0",
                        status="ok",
                        total_duration_ms=1.0,
                    )
                )

            path = Path(directory) / "domain-onboarding-2026-07-27.jsonl"
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([row["request_id"] for row in rows], ["request-0", "request-1"])

    def test_audit_failure_is_fail_open_and_visible_in_metrics(self) -> None:
        class FailingSink:
            def write(self, record: DomainOnboardingAuditRecord) -> None:
                raise OSError("disk unavailable")

        metrics = DomainOnboardingMetrics()
        app_state = SimpleNamespace(
            domain_onboarding_pipeline=object(),
            domain_onboarding_metrics=metrics,
            domain_onboarding_audit_sink=FailingSink(),
        )
        response = handle_domain_onboarding_message(
            ChannelMessage(
                session_id="session",
                user_id="user",
                channel="test",
                direction="inbound",
                mode="domain_onboarding",
                content="",
            ),
            app_state,
        )

        self.assertEqual(response["status"], "invalid_input")
        self.assertEqual(metrics.snapshot()["audit"]["write_failures"], 1)


if __name__ == "__main__":
    unittest.main()
