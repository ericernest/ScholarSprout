"""提供会话消息的最小 mode 分发入口。"""

from __future__ import annotations

from typing import Any

from bus.events import OUTBOUND
from channels.base import ChannelMessage
from handlers.chat_handler import handle_chat_message
from handlers.domain_onboarding_handler import handle_domain_onboarding_message
from handlers.paper_reading_handler import handle_paper_reading_message


# 根据 message.mode 分发到对应 handler。
def handle_message(message: ChannelMessage, app_state: Any) -> ChannelMessage:
    if message.mode == "chat":
        content = handle_chat_message(message, app_state)
    elif message.mode == "paper_reading":
        content = handle_paper_reading_message(message, app_state)
    elif message.mode == "domain_onboarding":
        content = handle_domain_onboarding_message(message, app_state)
    else:
        content = f"unsupported mode: {message.mode}"

    # handler 第一版只返回文本，这里统一包装为 outbound message。
    return ChannelMessage(
        session_id=message.session_id,
        channel=message.channel,
        direction=OUTBOUND,
        mode=message.mode,
        content=content,
        user_id=message.user_id,
    )
