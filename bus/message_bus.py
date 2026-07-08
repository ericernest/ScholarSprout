"""实现轻量内存消息总线。"""

from __future__ import annotations

from channels.base import ChannelMessage

from .events import INBOUND, OUTBOUND, MESSAGE_RECEIVED, MESSAGE_SENT, BusEvent


# 只负责内存事件记录和读取，不处理具体业务。
class MessageBus:
    # 初始化内存事件列表。
    def __init__(self) -> None:
        self._events: list[BusEvent] = []

    # 发布一条 bus 事件。
    def publish(self, event: BusEvent) -> None:
        self._events.append(event)

    # 根据 ChannelMessage 生成并记录对应事件。
    def publish_message(self, message: ChannelMessage) -> None:
        if message.direction == INBOUND:
            event_type = MESSAGE_RECEIVED
        elif message.direction == OUTBOUND:
            event_type = MESSAGE_SENT
        else:
            raise ValueError(f"Unsupported message direction: {message.direction}")

        self.publish(
            BusEvent(
                event_type=event_type,
                message=message,
                session_id=message.session_id,
            )
        )

    # 获取全部事件或指定 session 的事件。
    def get_events(self, session_id: str | None = None) -> list[BusEvent]:
        if session_id is None:
            return list(self._events)

        return [event for event in self._events if event.session_id == session_id]

    # 清空当前内存事件。
    def clear(self) -> None:
        self._events.clear()

    # 获取最近的若干条事件。
    def last_events(self, limit: int = 50) -> list[BusEvent]:
        return self._events[-limit:]
