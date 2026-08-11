from __future__ import annotations

import unittest

from handlers.domain_onboarding.llm import StructuredLLMError, invoke_json, parse_json_object

from .fakes import FakeJSONModel


class FailedLLMCallMetricsTests(unittest.TestCase):
    def test_parser_accepts_prefixed_complete_object(self) -> None:
        self.assertEqual(
            parse_json_object('result: {"domain":"检索增强生成"}\nfinished'),
            {"domain": "检索增强生成"},
        )

    def test_parser_accepts_fenced_complete_object(self) -> None:
        self.assertEqual(
            parse_json_object('```json\n{"domain":"RAG"}\n```'),
            {"domain": "RAG"},
        )

    def test_parser_repairs_unescaped_quotes_inside_paper_title(self) -> None:
        raw_text = (
            '{"activities":["Read CAMEL: Communicative Agents for '
            '"Mind" Exploration"],"evidence_claims":[]}'
        )

        self.assertEqual(
            parse_json_object(raw_text),
            {
                "activities": [
                    'Read CAMEL: Communicative Agents for "Mind" Exploration'
                ],
                "evidence_claims": [],
            },
        )

    def test_parser_rejects_inner_object_from_truncated_outer_object(self) -> None:
        raw_text = (
            '{"domain":"检索增强生成","prerequisites":['
            '{"name":"信息检索","why_needed":"理解召回"}'
        )

        self.assertIsNone(parse_json_object(raw_text))

    def test_streaming_json_forwards_batched_deltas(self) -> None:
        class StreamingModel:
            supports_streaming = True

            def chat_stream(self, **kwargs):
                self.kwargs = kwargs
                yield {"choices": [{"delta": {"content": '{"ok"'}}]}
                yield {
                    "choices": [{"delta": {"content": ":true}"}}],
                    "usage": {
                        "prompt_tokens": 2,
                        "completion_tokens": 3,
                        "total_tokens": 5,
                    },
                }

        deltas: list[tuple[str, str]] = []
        payload, stats = invoke_json(
            StreamingModel(),
            system_prompt="system",
            user_prompt="user",
            on_delta=lambda stage, delta: deltas.append((stage, delta)),
            stream_stage="planning",
        )

        self.assertEqual(payload, {"ok": True})
        self.assertEqual("".join(delta for _, delta in deltas), '{"ok":true}')
        self.assertEqual({stage for stage, _ in deltas}, {"planning"})
        self.assertEqual(stats.total_tokens, 5)

    def test_json_call_does_not_set_application_output_limit(self) -> None:
        model = FakeJSONModel([{"ok": True}])

        invoke_json(
            model,
            system_prompt="system",
            user_prompt="user",
        )

        self.assertNotIn("max_tokens", model.calls[0])
        self.assertEqual(
            model.calls[0]["response_format"],
            {"type": "json_object"},
        )

    def test_streaming_length_finish_reason_reports_truncation(self) -> None:
        class TruncatedStreamingModel:
            supports_streaming = True

            def chat_stream(self, **kwargs):
                yield {"choices": [{"delta": {"content": '{"stages":['}}]}
                yield {
                    "choices": [{"delta": {"content": '{"name":"first"}'}}]
                }
                yield {"choices": [{"delta": {}, "finish_reason": "length"}]}

        with self.assertRaisesRegex(
            StructuredLLMError,
            "truncated by the model provider",
        ):
            invoke_json(
                TruncatedStreamingModel(),
                system_prompt="system",
                user_prompt="user",
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
