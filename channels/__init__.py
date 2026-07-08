"""导出 NoviceSynapse channel 基础类型。"""

from .base import BaseChannel, ChannelMessage
from .web import WebChannel

__all__ = [
    "BaseChannel",
    "ChannelMessage",
    "WebChannel",
]
