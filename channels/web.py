"""实现 Web UI 对应的 channel 适配器。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bus.events import INBOUND

from .base import BaseChannel, ChannelMessage

if TYPE_CHECKING:
    from bus.message_bus import MessageBus


# 负责 Web UI 消息进入和系统响应出去。
class WebChannel(BaseChannel):
    name = "web"

    # 绑定当前 gateway 共享的 MessageBus。
    def __init__(self, bus: MessageBus):
        self.bus = bus

    # 启动 WebChannel，第一版由 FastAPI route 接收请求。
    def start(self) -> None:
        return None

    # 停止 WebChannel，第一版无额外连接需要关闭。
    def stop(self) -> None:
        return None

    # 根据 HTTP 请求内容创建 inbound ChannelMessage。
    def create_inbound_message(
        self,
        session_id: str,
        content: str,
        mode: str = "chat",
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ChannelMessage:
        return ChannelMessage(
            session_id=session_id,
            channel=self.name,
            direction=INBOUND,
            mode=mode,
            content=content,
            user_id=user_id,
            metadata=metadata or {},
        )

    # 将 Web inbound message 发布到 bus。
    def publish_inbound(self, message: ChannelMessage) -> None:
        self.bus.publish_message(message)

    # 第一版只记录 outbound，HTTP response 由 route 返回。
    def send_outbound(self, message: ChannelMessage) -> None:
        self.bus.publish_message(message)
