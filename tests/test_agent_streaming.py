from __future__ import annotations

import unittest
from threading import Event
from types import SimpleNamespace

from runtime.agent_runner import run_agent_detailed


class _Stream:
    def __init__(self, chunks: list[dict]) -> None:
        self.chunks = chunks
        self.closed = False

    def __iter__(self):
        return iter(self.chunks)

    def close(self) -> None:
        self.closed = True


class _StreamingModel:
    def __init__(self, turns: list[list[dict]]) -> None:
        self.turns = turns

    def chat_stream(self, **_kwargs):
        return _Stream(self.turns.pop(0))


class _ToolRegistry:
    def __init__(self) -> None:
        self.arguments = None

    def to_openai_tools(self, names):
        return [{"type": "function", "function": {"name": name}} for name in names]

    def get(self, _name):
        registry = self

        class Tool:
            def run(self, arguments):
                registry.arguments = arguments
                return {"ok": True}

        return Tool()


def _agent(model, tools=None):
    return SimpleNamespace(
        llm=model,
        profile=SimpleNamespace(
            system_prompt="system",
            tools=tools or [],
            default_skill="",
            skills=[],
            role="test",
            name="test",
        ),
    )


class AgentStreamingTests(unittest.TestCase):
    def test_streams_visible_text_and_reassembles_tool_calls(self) -> None:
        model = _StreamingModel(
            [
                [
                    {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call-1", "function": {"name": "echo", "arguments": '{"value":'}}]}}]},
                    {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": " 1}"}}]}}]},
                ],
                [
                    {"choices": [{"delta": {"reasoning_content": "先检查证据"}}]},
                    {"choices": [{"delta": {"content": "最终"}}]},
                    {"choices": [{"delta": {"content": "回答"}}]},
                ],
            ]
        )
        registry = _ToolRegistry()
        deltas: list[str] = []
        reasoning_deltas: list[str] = []

        result = run_agent_detailed(
            _agent(model, ["echo"]),
            "question",
            registry,
            max_steps=2,
            on_text_delta=deltas.append,
            on_reasoning_delta=reasoning_deltas.append,
            cancel_event=Event(),
        )

        self.assertEqual(result.text, "最终回答")
        self.assertEqual(deltas, ["最终", "回答"])
        self.assertEqual(reasoning_deltas, ["先检查证据"])
        self.assertEqual(result.reasoning, "先检查证据")
        self.assertEqual(registry.arguments, {"value": 1})
        self.assertFalse(result.cancelled)

    def test_pre_cancelled_stream_stops_without_completing(self) -> None:
        event = Event()
        event.set()
        result = run_agent_detailed(
            _agent(_StreamingModel([[{"choices": [{"delta": {"content": "不应输出"}}]}]])),
            "question",
            _ToolRegistry(),
            on_text_delta=lambda _delta: self.fail("cancelled stream emitted text"),
            cancel_event=event,
        )

        self.assertTrue(result.cancelled)
        self.assertEqual(result.text, "")

    def test_inline_think_tags_are_removed_from_final_answer(self) -> None:
        result = run_agent_detailed(
            _agent(_StreamingModel([[
                {"choices": [{"delta": {"content": "<think>内部推理"}}]},
                {"choices": [{"delta": {"content": "</think>最终答案"}}]},
            ]])),
            "question",
            _ToolRegistry(),
            on_text_delta=lambda _delta: None,
            cancel_event=Event(),
        )

        self.assertEqual(result.reasoning, "内部推理")
        self.assertEqual(result.text, "最终答案")


if __name__ == "__main__":
    unittest.main()
