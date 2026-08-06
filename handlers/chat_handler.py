"""提供 chat mode 的薄入口 handler。"""

from __future__ import annotations

from typing import Any

from channels.base import ChannelMessage
from runtime.agent_runner import run_agent_detailed


# 处理 chat mode 消息。
def handle_chat_message(message: ChannelMessage, app_state: Any) -> dict[str, str]:
    result = run_agent_detailed(
        agent=app_state.chat_agent,
        user_content=str(message.content),
        tool_registry=app_state.tool_registry,
        skill_registry=app_state.skill_registry,
        capability_selector=app_state.capability_selector,
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
