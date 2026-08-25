"""提供 chat mode 的薄入口 handler。"""

from __future__ import annotations

import logging
import json
from typing import Any

from channels.base import ChannelMessage
from runtime.agent_runner import run_agent_detailed


logger = logging.getLogger(__name__)


# 处理 chat mode 消息。
def handle_chat_message(message: ChannelMessage, app_state: Any) -> dict[str, str]:
    memory_text = ""
    context_messages: list[dict[str, str]] = []
    memory_service = getattr(app_state, "memory_service", None)
    if memory_service is not None:
        try:
            context = memory_service.prepare_context(
                message.session_id, exclude_message_id=message.message_id
            )
            memory_text = context.memory_text
            context_messages = context.context_messages
        except Exception:
            logger.exception("Failed to prepare conversation memory for %s", message.session_id)

    raw_active = message.metadata.get("active_context")
    active_context = {}
    if isinstance(raw_active, dict):
        active_context = {
            key: str(raw_active.get(key) or "")[:300]
            for key in ("kind", "id", "title")
            if str(raw_active.get(key) or "").strip()
        }
    request_context_text = (
        json.dumps(active_context, ensure_ascii=False) if active_context else ""
    )
    result = run_agent_detailed(
        agent=app_state.chat_agent,
        user_content=str(message.content),
        tool_registry=app_state.tool_registry,
        skill_registry=app_state.skill_registry,
        capability_selector=app_state.capability_selector,
        memory_text=memory_text,
        context_messages=context_messages,
        request_context_text=request_context_text,
        tool_context={
            "conversation_id": message.session_id,
            "active_context": active_context,
        },
        max_steps=3,
        on_text_delta=message.metadata.get("_stream_text_delta"),
        on_reasoning_delta=message.metadata.get("_stream_reasoning_delta"),
        cancel_event=message.metadata.get("_stream_cancel_event"),
    )

    return {
        "text": result.text,
        "reasoning": result.reasoning,
        "interrupted": result.cancelled,
        "status": "ok",
    }
