"""实现 Web UI 对应的 channel 适配器。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

from fastapi import HTTPException, Request
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

    # 启动 WebChannel，HTTP 监听由 gateway route 统一注册。
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

    # 从 FastAPI 请求中解析 Web inbound message。
    async def receive_message(self, source: Request, mode: str) -> ChannelMessage:
        request = source
        body = await request.json()
        metadata = body.get("metadata")

        return self.create_inbound_message(
            session_id=self._read_session_id(body),
            content=body if mode == "paper_reading" else str(body.get("content") or ""),
            mode=mode,
            user_id=body.get("user_id"),
            metadata=metadata if isinstance(metadata, dict) else None,
        )

    # 将 Web inbound message 发布到 bus。
    def publish_inbound(self, message: ChannelMessage) -> None:
        self.bus.publish_message(message)

    # 第一版只记录 outbound，HTTP response 由 route 返回。
    def send_outbound(self, message: ChannelMessage) -> None:
        self.bus.publish_message(message)

    # 从请求体中读取 session_id。
    def _read_session_id(self, body: dict[str, Any]) -> str:
        session_id = str(body.get("session_id") or "").strip()
        if not session_id:
            if str(body.get("action") or "").strip():
                return f"paper-reading-{uuid4()}"
            raise HTTPException(status_code=400, detail="session_id is required.")

        return session_id
