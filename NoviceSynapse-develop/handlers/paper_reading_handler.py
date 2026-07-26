"""提供 paper_reading mode 的 handler — 委托到 paper_reading 模块。"""

from __future__ import annotations

from typing import Any

from channels.base import ChannelMessage


def handle_paper_reading_message(
    message: ChannelMessage,
    app_state: Any,
) -> dict[str, Any]:
    """论文精读 handler — 委托到 paper_reading.handler 模块。

    框架接口: (ChannelMessage, app_state) -> dict
    """
    from paper_reading.handler import handle_paper_reading_message as _impl
    return _impl(message, app_state)
