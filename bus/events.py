"""定义 NoviceSynapse 内部 bus 事件类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from channels.base import ChannelMessage

INBOUND = "inbound"
OUTBOUND = "outbound"
MESSAGE_RECEIVED = "message.received"
MESSAGE_SENT = "message.sent"
AGENT_STARTED = "agent.started"
AGENT_FINISHED = "agent.finished"
TASK_PROGRESS = "task.progress"
TASK_FAILED = "task.failed"


# 表示 bus 内部记录的一条事件。
@dataclass(slots=True)
class BusEvent:
    event_type: str
    message: ChannelMessage | None = None
    session_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
