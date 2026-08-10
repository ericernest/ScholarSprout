"""Persist user-visible channel exchanges without provider or reasoning payloads."""

from __future__ import annotations

import json
import logging
from typing import Any

from channels.base import ChannelMessage

logger = logging.getLogger(__name__)


def record_inbound(app_state: Any, message: ChannelMessage) -> None:
    store = getattr(app_state, "research_storage", None)
    # Paper-reading HTTP actions include parsing/status utilities whose temporary
    # transport ids are not user-opened conversations. The reading handler records
    # actual Agent Q&A after it has resolved the persistent reading session id.
    if store is None or message.mode == "paper_reading":
        return
    try:
        text = message_text(message.mode, message.content, outbound=False)
        store.ensure_conversation(
            message.session_id,
            title=_conversation_title(message.mode, text),
            user_id=message.user_id,
        )
        store.append_message(
            message.session_id,
            role="user",
            content=text,
            mode=message.mode,
            channel=message.channel,
            message_id=message.message_id,
        )
    except Exception:
        logger.exception("Failed to persist inbound %s message", message.mode)


def record_outbound(app_state: Any, message: ChannelMessage) -> None:
    store = getattr(app_state, "research_storage", None)
    if store is None or message.mode == "paper_reading":
        return
    try:
        store.append_message(
            message.session_id,
            role="assistant",
            content=message_text(message.mode, message.content, outbound=True),
            mode=message.mode,
            channel=message.channel,
            message_id=message.message_id,
        )
    except Exception:
        logger.exception("Failed to persist outbound %s message", message.mode)


def message_text(mode: str, content: Any, *, outbound: bool) -> str:
    if isinstance(content, str):
        return content.strip() or "（空消息）"
    if not isinstance(content, dict):
        return str(content)
    if outbound:
        for value in (
            content.get("text"),
            content.get("message"),
            (content.get("data") or {}).get("agent_response")
            if isinstance(content.get("data"), dict)
            else None,
        ):
            if isinstance(value, str) and value.strip():
                return value.strip()
        summary = {
            key: content[key]
            for key in ("status", "action", "session_id", "paper_id", "error")
            if content.get(key) not in (None, "")
        }
        return json.dumps(summary or {"status": "completed"}, ensure_ascii=False)
    if mode == "paper_reading":
        safe = {
            key: content[key]
            for key in (
                "action",
                "paper_id",
                "content",
                "search_query",
                "target_section",
                "fork_context",
                "fork_question",
                "merge_session_id",
            )
            if content.get(key) not in (None, "", [], {})
        }
        return json.dumps(safe or {"action": "start_reading"}, ensure_ascii=False)
    return str(content.get("content") or content.get("query") or "").strip() or "（空消息）"


def _conversation_title(mode: str, text: str) -> str:
    compact = " ".join(text.split())
    return compact[:60] or "新会话"
