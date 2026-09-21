from __future__ import annotations

from handlers.chat_handler import (
    handle_chat_message,
)
from handlers.domain_onboarding_handler import (
    handle_domain_onboarding_message,
)
from gateway.feishu_router import handle_feishu_mode_message


def handle_qq_mode_message(
    message,
    app_state,
    *,
    mode: str = "chat",
    download_qq_file=None,
):
    """
    QQ 业务路由。

    QQ 只负责入口适配，
    真正业务逻辑继续复用现有 Handler。
    """

    # =========================================================
    # 普通聊天
    # =========================================================

    if mode == "chat":

        message.mode = "chat"

        return handle_chat_message(
            message,
            app_state,
        )

    # =========================================================
    # 领域入门
    # =========================================================

    if mode == "domain_onboarding":

        message.mode = (
            "domain_onboarding"
        )

        return (
            handle_domain_onboarding_message(
                message,
                app_state,
            )
        )

    # =========================================================
    # 论文精读
    #
    # PDF/URL 复用飞书端已经完成的论文输入适配，避免复制
    # upload_paper -> parse -> create_session -> start_reading 流程。
    # =========================================================

    if mode == "paper_reading":

        message.mode = (
            "paper_reading"
        )

        if isinstance(message.content, dict):
            file_url = str(
                message.content.get("file_url")
                or message.content.get("url")
                or ""
            ).strip()
            file_name = str(
                message.content.get("file_name")
                or message.content.get("filename")
                or "paper.pdf"
            ).strip()

            if not file_url:
                return {
                    "text": "没有找到可下载的 PDF 附件地址。",
                    "status": "error",
                }

            message.content = {
                # 复用飞书 Router 的文件输入约定；这里的 file_key 仅在
                # 适配器内部作为 QQ 附件 URL 传给下载回调。
                "file_key": file_url,
                "file_name": file_name,
            }
            message.metadata.setdefault(
                "feishu_message_id",
                str(message.metadata.get("qq_message_id") or "qq"),
            )

        if callable(download_qq_file):
            def download_adapter(_message_id, file_key):
                return download_qq_file(str(file_key or ""))
        else:
            download_adapter = None

        return handle_feishu_mode_message(
            message,
            app_state,
            mode="paper_reading",
            download_feishu_file=download_adapter,
        )

    # =========================================================
    # Fallback
    # =========================================================

    message.mode = "chat"

    return handle_chat_message(
        message,
        app_state,
    )
