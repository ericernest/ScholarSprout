"""导出 NoviceSynapse 消息 handler。"""

from .chat_handler import handle_chat_message
from .domain_onboarding_handler import handle_domain_onboarding_message
from .paper_reading_handler import handle_paper_reading_message

__all__ = [
    "handle_chat_message",
    "handle_domain_onboarding_message",
    "handle_paper_reading_message",
]
