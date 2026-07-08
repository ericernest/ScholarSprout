"""提供 paper_reading mode 的占位 handler。"""

from __future__ import annotations

from typing import Any

from channels.base import ChannelMessage


# 处理 paper_reading mode 消息。
def handle_paper_reading_message(message: ChannelMessage, app_state: Any) -> dict[str, str]:
    return {
        "text": "paper reading handler is not implemented yet",
        "status": "not_implemented",
    }
