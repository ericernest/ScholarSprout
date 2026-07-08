"""提供 chat mode 的薄入口 handler。"""

from __future__ import annotations

from typing import Any

from channels.base import ChannelMessage
from runtime.agent_runner import run_agent


# 处理 chat mode 消息。
def handle_chat_message(message: ChannelMessage, app_state: Any) -> dict[str, str]:
    text = run_agent(
        agent=app_state.chat_agent,
        user_content=str(message.content),
        model=app_state.model,
        tool_registry=app_state.tool_registry,
        max_steps=3,
    )

    return {
        "text": text,
        "status": "ok",
    }
