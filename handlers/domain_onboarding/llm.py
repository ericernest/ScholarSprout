"""领域入门模块共享的结构化 LLM 调用辅助函数。"""

from __future__ import annotations

import json
from time import perf_counter
from typing import Any

from runtime.agent_runner import get_message_content, get_response_message, get_response_usage

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
        response = model.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            timeout=timeout_seconds,
        )
    except Exception as error:
        stats = ModelCallStats(
            duration_ms=round((perf_counter() - started) * 1000, 3),
            model_calls=1,
        )
        raise StructuredLLMError(f"LLM call failed: {error}", stats) from error

    usage = get_response_usage(response)
    stats = ModelCallStats(
        duration_ms=round((perf_counter() - started) * 1000, 3),
        model_calls=1,
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
