from __future__ import annotations

import unittest

from handlers.domain_onboarding.llm import StructuredLLMError, invoke_json

from .fakes import FakeJSONModel


class FailedLLMCallMetricsTests(unittest.TestCase):
    def test_max_tokens_is_forwarded_to_model(self) -> None:
        model = FakeJSONModel([{"ok": True}])

        invoke_json(
            model,
            system_prompt="system",
            user_prompt="user",
            max_tokens=1234,
        )

        self.assertEqual(model.calls[0]["max_tokens"], 1234)
        self.assertEqual(
            model.calls[0]["response_format"],
            {"type": "json_object"},
        )

    def test_invalid_json_error_preserves_reported_usage(self) -> None:
        model = FakeJSONModel(["not json"])

        with self.assertRaises(StructuredLLMError) as raised:
            invoke_json(model, system_prompt="system", user_prompt="user")

        stats = raised.exception.stats
        self.assertEqual(stats.model_calls, 1)
        self.assertEqual(stats.prompt_tokens, 30)
        self.assertEqual(stats.completion_tokens, 20)
        self.assertEqual(stats.total_tokens, 50)
        self.assertTrue(stats.usage_reported)

    def test_transport_error_counts_call_with_unreported_usage(self) -> None:
        model = FakeJSONModel([RuntimeError("offline")])

        with self.assertRaises(StructuredLLMError) as raised:
            invoke_json(model, system_prompt="system", user_prompt="user")

        stats = raised.exception.stats
        self.assertEqual(stats.model_calls, 1)
        self.assertEqual(stats.total_tokens, 0)
        self.assertFalse(stats.usage_reported)


if __name__ == "__main__":
    unittest.main()
