from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evaluation.domain_onboarding.capture_async_example import (
    _persist_terminal_capture,
)


class CaptureAsyncExampleTests(unittest.TestCase):
    def test_terminal_capture_is_written_before_publication_decision(self) -> None:
        snapshot = {
            "state": "completed",
            "result": {
                "status": "quality_failed",
                "quality": {"passed_hard_gates": False},
            },
        }
        events = [
            {
                "event": "completed",
                "data": {"result_available": True},
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            snapshot_path = Path(directory) / "snapshot.json"
            events_path = Path(directory) / "events.jsonl"

            _persist_terminal_capture(
                snapshot,
                events,
                snapshot_output=snapshot_path,
                events_output=events_path,
            )

            saved_snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            saved_event = json.loads(events_path.read_text(encoding="utf-8"))

        self.assertEqual(saved_snapshot, snapshot)
        self.assertEqual(saved_event["data"]["result"], snapshot["result"])


if __name__ == "__main__":
    unittest.main()
