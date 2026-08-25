"""Rolling memory service over persisted, user-visible conversation messages."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from memory.prompts import (
    COMPRESSION_SYSTEM_PROMPT,
    MEMORY_SCHEMA_VERSION,
    compression_user_prompt,
    render_memory,
)

logger = logging.getLogger(__name__)

_REDACTED = "[已隐藏敏感信息]"
_QUOTED_SECRET_RE = re.compile(
    r'''(?i)((?:["']?)(?:api[_-]?key|password|secret|token)(?:["']?)\s*[:=]\s*)(["'])(.*?)(\2)'''
)
_UNQUOTED_SECRET_RE = re.compile(
    r'''(?i)((?:["']?)(?:api[_-]?key|password|secret|token)(?:["']?)\s*[:=]\s*)(?!["'])([^\s,;}\]]+)'''
)
_BEARER_RE = re.compile(
    r"(?i)(\b(?:authorization\s*[:=]\s*)?bearer\s+)([A-Za-z0-9._~+/=-]{8,})"
)
_SK_TOKEN_RE = re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{8,}\b")


class MemoryCompressionError(RuntimeError):
    """Compression failed without advancing the persisted watermark."""


@dataclass(slots=True)
class ConversationContext:
    memory_text: str = ""
    context_messages: list[dict[str, str]] = field(default_factory=list)
    compression_failed: bool = False


class ConversationMemoryService:
    """Keep compact memory plus the latest N visible messages for one conversation."""

    def __init__(self, store: Any, model: Any, recent_message_limit: int = 8) -> None:
        self.store = store
        self.model = model
        self.recent_message_limit = max(1, int(recent_message_limit))
        self._locks_guard = Lock()
        self._conversation_locks: dict[str, Lock] = {}

    def prepare_context(
        self,
        conversation_id: str,
        *,
        exclude_message_id: str | None = None,
    ) -> ConversationContext:
        with self._lock_for(conversation_id):
            return self._prepare_context_unlocked(
                conversation_id, exclude_message_id=exclude_message_id
            )

    def _prepare_context_unlocked(
        self,
        conversation_id: str,
        *,
        exclude_message_id: str | None,
    ) -> ConversationContext:
        """Compact overflow before a model call; failure degrades without losing history."""
        window = self.store.read_conversation_memory_window(
            conversation_id, recent_limit=self.recent_message_limit
        )
        prior_memory = self._sanitized_memory(window.get("memory"))
        compression_failed = False
        if window["messages_to_compress"]:
            try:
                memory = self._compress(
                    prior_memory, window["messages_to_compress"], final=False
                )
                self.store.save_latest_memory(
                    conversation_id,
                    memory=memory,
                    through_message_id=window["messages_to_compress"][-1]["message_id"],
                    expected_previous_through_message_id=window.get("through_message_id"),
                )
                window = self.store.read_conversation_memory_window(
                    conversation_id, recent_limit=self.recent_message_limit
                )
            except Exception:
                compression_failed = True
                logger.exception("Conversation memory compression failed for %s", conversation_id)

        history = (
            [*window["messages_to_compress"], *window["recent_messages"]]
            if compression_failed
            else window["recent_messages"]
        )
        return ConversationContext(
            memory_text=render_memory(
                self._sanitized_memory(window.get("memory")),
                [
                    self._sanitized_memory(linked) or {}
                    for linked in window.get("linked_forks", [])
                ],
            ),
            context_messages=self._context_messages(history, exclude_message_id),
            compression_failed=compression_failed,
        )

    def finalize_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        with self._lock_for(conversation_id):
            return self._finalize_conversation_unlocked(conversation_id)

    def _finalize_conversation_unlocked(
        self, conversation_id: str
    ) -> dict[str, Any] | None:
        """Compress every visible message not already covered by the watermark."""
        window = self.store.read_conversation_memory_window(conversation_id, recent_limit=1)
        remaining = [*window["messages_to_compress"], *window["recent_messages"]]
        if not remaining:
            if window.get("memory"):
                return self._sanitized_memory(window["memory"]) or {}
            return None
        memory = self._compress(
            self._sanitized_memory(window.get("memory")), remaining, final=True
        )
        self.store.save_latest_memory(
            conversation_id,
            memory=memory,
            through_message_id=remaining[-1]["message_id"],
            expected_previous_through_message_id=window.get("through_message_id"),
        )
        stored = self.store.get_latest_memory(conversation_id)
        if stored is None:
            raise MemoryCompressionError("Final memory was not persisted")
        return stored

    def _compress(
        self,
        prior_memory: dict[str, Any] | None,
        messages: list[dict[str, Any]],
        *,
        final: bool,
    ) -> dict[str, Any]:
        if not messages:
            return self._sanitize(prior_memory or {})
        response = self.model.chat(
            messages=[
                {"role": "system", "content": COMPRESSION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": compression_user_prompt(prior_memory or {}, messages, final=final),
                },
            ],
            response_format={"type": "json_object"},
            disable_thinking=True,
        )
        content = self._response_content(response)
        try:
            payload = json.loads(content)
        except (TypeError, json.JSONDecodeError) as error:
            raise MemoryCompressionError("Memory model did not return valid JSON") from error
        if not isinstance(payload, dict):
            raise MemoryCompressionError("Memory model returned a non-object")
        return self._sanitize(payload)

    @staticmethod
    def _response_content(response: Any) -> str:
        choices = response.get("choices", []) if isinstance(response, dict) else getattr(response, "choices", [])
        if not choices:
            raise MemoryCompressionError("Memory model returned no choices")
        first = choices[0]
        message = first.get("message", {}) if isinstance(first, dict) else getattr(first, "message", {})
        value = message.get("content", "") if isinstance(message, dict) else getattr(message, "content", "")
        return str(value or "")

    @classmethod
    def _sanitize(cls, payload: dict[str, Any]) -> dict[str, Any]:
        goal = cls._text(payload.get("current_goal"), 300)
        decisions = cls._items(payload.get("confirmed_decisions"), limit=12)
        decision_keys = {cls._key(item) for item in decisions}
        questions = [
            item
            for item in cls._items(payload.get("open_questions"), limit=12)
            if cls._key(item) not in decision_keys
        ]
        occupied = {cls._key(goal), *decision_keys, *(cls._key(item) for item in questions)}
        summary_lines = [
            line.strip()
            for line in cls._text(payload.get("summary"), 2400).splitlines()
            if line.strip() and cls._key(line) not in occupied
        ]
        return {
            "schema_version": MEMORY_SCHEMA_VERSION,
            "current_goal": goal,
            "summary": "\n".join(dict.fromkeys(summary_lines))[:2000],
            "confirmed_decisions": decisions,
            "open_questions": questions,
        }

    @classmethod
    def _sanitized_memory(
        cls, payload: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        if not payload:
            return None
        return {**payload, **cls._sanitize(payload)}

    @classmethod
    def _text(cls, value: Any, limit: int) -> str:
        return " ".join(cls._redact(str(value or "")).split())[:limit]

    @staticmethod
    def _redact(value: str) -> str:
        text = _QUOTED_SECRET_RE.sub(
            lambda match: f"{match.group(1)}{match.group(2)}{_REDACTED}{match.group(2)}",
            value,
        )
        text = _UNQUOTED_SECRET_RE.sub(
            lambda match: f"{match.group(1)}{_REDACTED}", text
        )
        text = _BEARER_RE.sub(
            lambda match: f"{match.group(1)}{_REDACTED}", text
        )
        return _SK_TOKEN_RE.sub(_REDACTED, text)

    @classmethod
    def _items(cls, value: Any, *, limit: int) -> list[str]:
        source = value if isinstance(value, list) else []
        result: list[str] = []
        seen: set[str] = set()
        for raw in source:
            text = cls._text(raw, 300)
            key = cls._key(text)
            if text and key not in seen:
                result.append(text)
                seen.add(key)
            if len(result) >= limit:
                break
        return result

    @staticmethod
    def _key(value: str) -> str:
        return "".join(str(value or "").casefold().split()).strip(".,;:!?，。；：！？")

    @staticmethod
    def _context_messages(
        messages: list[dict[str, Any]], exclude_message_id: str | None
    ) -> list[dict[str, str]]:
        context: list[dict[str, str]] = []
        for item in messages:
            role = str(item.get("role") or "")
            content = str(item.get("content") or "")
            if (
                item.get("message_id") == exclude_message_id
                or role not in {"user", "assistant", "tool"}
                or not content.strip()
            ):
                continue
            if role == "tool":
                # Persisted visible tool messages do not retain tool_call_id,
                # so replay them as historical assistant data instead of an
                # invalid bare Chat Completions tool message.
                role = "assistant"
                content = f"[工具结果]\n{content}"
            context.append({"role": role, "content": content})
        return context

    def _lock_for(self, conversation_id: str) -> Lock:
        with self._locks_guard:
            lock = self._conversation_locks.get(conversation_id)
            if lock is None:
                lock = Lock()
                self._conversation_locks[conversation_id] = lock
            return lock
