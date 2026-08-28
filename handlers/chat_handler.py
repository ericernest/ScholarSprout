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

    raw_contexts = message.metadata.get("active_contexts")
    if not isinstance(raw_contexts, list):
        legacy = message.metadata.get("active_context")
        raw_contexts = [legacy] if isinstance(legacy, dict) else []
    active_contexts: list[dict[str, str]] = []
    seen_contexts: set[tuple[str, str]] = set()
    for raw_active in raw_contexts[:20]:
        if not isinstance(raw_active, dict):
            continue
        active = {
            key: " ".join(
                str(raw_active.get(key) or "").replace("<", "").replace(">", "").split()
            )[:300]
            for key in ("kind", "id", "title")
            if str(raw_active.get(key) or "").strip()
        }
        identity = (active.get("kind", ""), active.get("id", ""))
        if identity[0] in {"domain_onboarding", "paper_reading"} and identity[1] and identity not in seen_contexts:
            active_contexts.append(active)
            seen_contexts.add(identity)
    request_context_text = json.dumps(active_contexts, ensure_ascii=False) if active_contexts else ""
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
            "active_contexts": active_contexts,
            "active_context": active_contexts[0] if len(active_contexts) == 1 else {},
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
