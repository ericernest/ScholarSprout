"""定义外部 channel 消息与适配器基础接口。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from bus.message_bus import MessageBus


# 表示外部 channel 进入或离开系统时的统一消息。
@dataclass(slots=True)
class ChannelMessage:
    session_id: str
    channel: str
    direction: str
    mode: str
    content: Any
    user_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    message_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# 定义所有外部 channel 需要实现的最小接口。
class BaseChannel:
    name: str
    bus: MessageBus

    # 启动 channel 监听。
    def start(self) -> None:
        raise NotImplementedError

    # 停止 channel 监听。
    def stop(self) -> None:
        raise NotImplementedError

    # 将外部收到的 inbound message 发布到 bus。
    def publish_inbound(self, message: ChannelMessage) -> None:
        raise NotImplementedError

    # 将 outbound message 发送回外部平台。
    def send_outbound(self, message: ChannelMessage) -> None:
        raise NotImplementedError
