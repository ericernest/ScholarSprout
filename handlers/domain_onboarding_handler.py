"""提供 domain_onboarding mode 的占位 handler。"""

from __future__ import annotations

from typing import Any

from channels.base import ChannelMessage


# 处理 domain_onboarding mode 消息。
def handle_domain_onboarding_message(message: ChannelMessage, app_state: Any) -> dict[str, str]:
    return {
        "text": "domain onboarding handler is not implemented yet",
        "status": "not_implemented",
    }
