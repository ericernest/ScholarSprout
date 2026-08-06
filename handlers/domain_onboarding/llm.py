"""领域入门模块共享的结构化 LLM 调用辅助函数。"""

from __future__ import annotations

import json
from time import perf_counter
from typing import Any

from runtime.agent_runner import get_message_content, get_response_message, get_response_usage

from .execution import current_cancel_event
from .schemas import ModelCallStats


class StructuredLLMError(RuntimeError):
    def __init__(self, message: str, stats: ModelCallStats):
        super().__init__(message)
        self.stats = stats


def parse_json_object(raw_text: str) -> dict[str, Any] | None:
    text = raw_text.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        return parsed

    decoder = json.JSONDecoder()
    offset = 0
    while True:
        start = text.find("{", offset)
        if start < 0:
            return None
        try:
            candidate, _ = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            offset = start + 1
            continue
        if isinstance(candidate, dict):
            return candidate
        offset = start + 1


def invoke_json(
    model: Any,
    *,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int | None = None,
    timeout_seconds: float | None = None,
) -> tuple[dict[str, Any], ModelCallStats]:
    started = perf_counter()
    try:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        if callable(getattr(model, "chat_stream", None)) and getattr(
            model, "supports_streaming", True
        ):
            stream = model.chat_stream(
                messages=messages,
                max_tokens=max_tokens,
                timeout=timeout_seconds,
                response_format={"type": "json_object"},
            )
            parts: list[str] = []
            usage = None
            try:
                for chunk in stream:
                    cancel_event = current_cancel_event()
                    if cancel_event is not None and cancel_event.is_set():
                        raise RuntimeError("LLM call cancelled")
                    usage = getattr(chunk, "usage", None) or (
                        chunk.get("usage") if isinstance(chunk, dict) else None
                    ) or usage
                    choices = getattr(chunk, "choices", None) or (
                        chunk.get("choices", []) if isinstance(chunk, dict) else []
                    )
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = getattr(choice, "delta", None) or (
                        choice.get("delta", {}) if isinstance(choice, dict) else {}
                    )
                    content = getattr(delta, "content", None) or (
                        delta.get("content", "") if isinstance(delta, dict) else ""
                    )
                    if content:
                        parts.append(str(content))
            finally:
                close = getattr(stream, "close", None)
                if callable(close):
                    close()
            response = {
                "choices": [{"message": {"role": "assistant", "content": "".join(parts)}}],
                "usage": usage,
            }
        else:
            response = model.chat(
                messages=messages,
                max_tokens=max_tokens,
                timeout=timeout_seconds,
                response_format={"type": "json_object"},
            )
    except Exception as error:
        attempt_count = int(getattr(model, "last_attempt_count", 1))
        stats = ModelCallStats(
            duration_ms=round((perf_counter() - started) * 1000, 3),
            model_calls=attempt_count,
        )
        raise StructuredLLMError(f"LLM call failed: {error}", stats) from error

    usage = get_response_usage(response)
    attempt_count = int(getattr(model, "last_attempt_count", 1))
    stats = ModelCallStats(
        duration_ms=round((perf_counter() - started) * 1000, 3),
        model_calls=attempt_count,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        usage_reported=usage.reported,
    )
    content = get_message_content(get_response_message(response))
    parsed = parse_json_object(content)
    if parsed is None:
        raise StructuredLLMError("LLM did not return a JSON object", stats)
    return parsed, stats
