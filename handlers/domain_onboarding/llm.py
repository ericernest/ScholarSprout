"""领域入门模块共享的结构化 LLM 调用辅助函数。"""

from __future__ import annotations

import json
from time import perf_counter
from typing import TYPE_CHECKING, Any, Callable

from runtime.agent_runner import get_message_content, get_response_message, get_response_usage

from .execution import current_cancel_event
from .schemas import ModelCallStats

if TYPE_CHECKING:
    from .structured_response import ResponseContract


class StructuredLLMError(RuntimeError):
    def __init__(self, message: str, stats: ModelCallStats):
        super().__init__(message)
        self.stats = stats


def _escape_unquoted_inner_quotes(text: str) -> str:
    """Repair quotes inside an otherwise JSON-style string.

    Models occasionally copy a paper title such as ``for "Mind" Exploration``
    without escaping the inner pair. A quote can only close a JSON string when
    the next non-whitespace character is a structural delimiter. Preserve those
    closing quotes and escape the others; parsing still rejects truncated JSON
    because this function never invents missing brackets or closing quotes.
    """

    repaired: list[str] = []
    in_string = False
    escaped = False
    length = len(text)
    for index, character in enumerate(text):
        if not in_string:
            repaired.append(character)
            if character == '"':
                in_string = True
            continue
        if escaped:
            repaired.append(character)
            escaped = False
            continue
        if character == "\\":
            repaired.append(character)
            escaped = True
            continue
        if character != '"':
            repaired.append(character)
            continue

        next_index = index + 1
        while next_index < length and text[next_index].isspace():
            next_index += 1
        next_character = text[next_index] if next_index < length else ""
        if not next_character or next_character in ":,}]":
            repaired.append(character)
            in_string = False
        else:
            repaired.append('\\"')
    return "".join(repaired)


def _load_json_object(candidate: str) -> dict[str, Any] | None:
    for text in (candidate, _escape_unquoted_inner_quotes(candidate)):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _get_finish_reason(response: Any) -> str:
    choices = getattr(response, "choices", None) or (
        response.get("choices", []) if isinstance(response, dict) else []
    )
    if not choices:
        return ""
    choice = choices[0]
    reason = getattr(choice, "finish_reason", None) or (
        choice.get("finish_reason") if isinstance(choice, dict) else None
    )
    return str(reason or "")


def parse_json_object(raw_text: str) -> dict[str, Any] | None:
    text = raw_text.strip()
    if not text:
        return None
    parsed = _load_json_object(text)
    if parsed is not None:
        return parsed

    # Only inspect balanced top-level object candidates. Advancing from a
    # truncated outer object to one of its valid inner objects silently turns
    # an incomplete model response into a plausible but structurally wrong
    # payload (for example, one prerequisite instead of the whole section).
    depth = 0
    array_depth = 0
    start: int | None = None
    in_string = False
    escaped = False
    for index, character in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "[" and depth == 0:
            array_depth += 1
        elif character == "]" and depth == 0 and array_depth:
            array_depth -= 1
        elif character == "{" and array_depth == 0:
            if depth == 0:
                start = index
            depth += 1
        elif character == "}" and array_depth == 0 and depth:
            depth -= 1
            if depth == 0 and start is not None:
                candidate = _load_json_object(text[start : index + 1])
                if candidate is not None:
                    return candidate
                start = None
    return None


def invoke_json(
    model: Any,
    *,
    system_prompt: str,
    user_prompt: str,
    timeout_seconds: float | None = None,
    on_delta: Callable[[str, str], None] | None = None,
    stream_stage: str = "generation",
    contract: ResponseContract | None = None,
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
                timeout=timeout_seconds,
                response_format={"type": "json_object"},
                disable_thinking=True,
            )
            parts: list[str] = []
            pending_parts: list[str] = []
            pending_chars = 0
            last_emitted_at = perf_counter()
            usage = None
            finish_reason = ""

            def flush_delta(*, force: bool = False) -> None:
                nonlocal pending_chars, last_emitted_at
                if on_delta is None or not pending_parts:
                    return
                if not force and pending_chars < 64 and perf_counter() - last_emitted_at < 0.12:
                    return
                delta_text = "".join(pending_parts)
                pending_parts.clear()
                pending_chars = 0
                last_emitted_at = perf_counter()
                on_delta(stream_stage, delta_text)

            try:
                for chunk in stream:
                    if (
                        timeout_seconds is not None
                        and perf_counter() - started >= timeout_seconds
                    ):
                        raise TimeoutError(
                            f"structured streaming call exceeded {timeout_seconds:.1f}s"
                        )
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
                    chunk_finish_reason = getattr(choice, "finish_reason", None) or (
                        choice.get("finish_reason") if isinstance(choice, dict) else None
                    )
                    if chunk_finish_reason:
                        finish_reason = str(chunk_finish_reason)
                    delta = getattr(choice, "delta", None) or (
                        choice.get("delta", {}) if isinstance(choice, dict) else {}
                    )
                    content = getattr(delta, "content", None) or (
                        delta.get("content", "") if isinstance(delta, dict) else ""
                    )
                    if content:
                        text = str(content)
                        parts.append(text)
                        pending_parts.append(text)
                        pending_chars += len(text)
                        flush_delta()
            finally:
                try:
                    flush_delta(force=True)
                finally:
                    close = getattr(stream, "close", None)
                    if callable(close):
                        close()
            response = {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "".join(parts)},
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": usage,
            }
        else:
            response = model.chat(
                messages=messages,
                timeout=timeout_seconds,
                response_format={"type": "json_object"},
                disable_thinking=True,
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
        if _get_finish_reason(response) == "length":
            raise StructuredLLMError(
                "LLM JSON response was truncated by the model provider; the application did not set max_tokens",
                stats,
            )
        raise StructuredLLMError("LLM did not return a JSON object", stats)
    if contract is not None:
        from .structured_response import (
            StructuredResponseError,
            adapt_structured_response,
        )

        try:
            parsed = adapt_structured_response(parsed, contract).data
        except StructuredResponseError as error:
            raise StructuredLLMError(
                f"structured response validation failed: {error}", stats
            ) from error
    return parsed, stats
