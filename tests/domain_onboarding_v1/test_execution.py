from __future__ import annotations

import unittest

from handlers.domain_onboarding.execution import (
    PipelineCancelled,
    PipelineDeadlineExceeded,
    PipelineExecutionContext,
)


class ExecutionContextTests(unittest.TestCase):
    def test_total_deadline_is_shared_across_stages(self) -> None:
        now = [0.0]
        context = PipelineExecutionContext(timeout_seconds=1.0, clock=lambda: now[0])

        def advance(seconds: float) -> str:
            now[0] += seconds
            return "ok"

        result, duration_ms = context.call("planning", 0.8, advance, 0.6)
        self.assertEqual(result, "ok")
        self.assertEqual(duration_ms, 600.0)

        with self.assertRaises(PipelineDeadlineExceeded) as raised:
            context.call("retrieval", 0.8, advance, 0.5)

        self.assertEqual(raised.exception.stage, "retrieval")
        self.assertEqual(raised.exception.duration_ms, 500.0)

    def test_stage_budget_can_expire_before_total_deadline(self) -> None:
        now = [0.0]
        context = PipelineExecutionContext(timeout_seconds=10.0, clock=lambda: now[0])

        with self.assertRaises(PipelineDeadlineExceeded) as raised:
            context.call("generation", 1.0, lambda: now.__setitem__(0, 1.1))

        self.assertEqual(raised.exception.stage, "generation")

    def test_cancel_signal_is_checked_before_starting_stage(self) -> None:
        context = PipelineExecutionContext(timeout_seconds=10.0)
        context.cancel()

        with self.assertRaises(PipelineCancelled):
            context.call("retrieval", 5.0, lambda: None)


if __name__ == "__main__":
    unittest.main()
