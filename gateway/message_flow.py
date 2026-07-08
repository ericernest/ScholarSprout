"""提供 gateway 中 channel message 的统一处理流程。"""

from __future__ import annotations

from typing import Any, Callable

from bus.events import OUTBOUND
from channels.base import BaseChannel, ChannelMessage

MessageHandler = Callable[[ChannelMessage, Any], Any]


# 将 handler 输出包装成 outbound message。
def build_channel_output(message: ChannelMessage, content: Any) -> ChannelMessage:
    return ChannelMessage(
        session_id=message.session_id,
        channel=message.channel,
        direction=OUTBOUND,
        mode=message.mode,
        content=content,
        user_id=message.user_id,
    )


# 执行 channel inbound、handler 处理、channel outbound 的标准流程。
def process_channel_message(
    channel: BaseChannel,
    message: ChannelMessage,
    handler: MessageHandler,
    app_state: Any,
) -> ChannelMessage:
    channel.publish_inbound(message)
    content = handler(message, app_state)
    outbound_message = build_channel_output(message, content)
    channel.send_outbound(outbound_message)

    return outbound_message


# 处理任意 channel 原始输入，具体 channel 由启动阶段注册。
async def process_channel_input(
    source: Any,
    mode: str,
    handler: MessageHandler,
    app_state: Any,
    channel_name: str | None = None,
) -> ChannelMessage:
    selected_channel_name = channel_name or app_state.default_channel_name
    channel = app_state.channels[selected_channel_name]
    inbound_message = await channel.receive_message(source, mode)

    return process_channel_message(
        channel=channel,
        message=inbound_message,
        handler=handler,
        app_state=app_state,
    )
