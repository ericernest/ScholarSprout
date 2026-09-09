from __future__ import annotations

import base64
import re
import time
from typing import Any, Callable

from channels.base import ChannelMessage
from handlers.chat_handler import handle_chat_message
from handlers.domain_onboarding_handler import handle_domain_onboarding_message
from handlers.paper_reading.handler import handle_paper_reading_message
from handlers.paper_reading.pdf_download import download_pdf_bytes


_PAPER_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def parse_mode_switch(text: str) -> tuple[str | None, str]:
    """
    识别飞书自然语言模式切换，并保留切换命令后面的正文。
    """
    raw = str(text or "").strip()

    patterns = (
        (
            "paper_reading",
            r"^\s*(?:请)?(?:切换到|切换为|切换|进入|开始)"
            r"(?:论文精读模式|论文精读)"
            r"\s*[，,：:\s]*(.*)$",
        ),
        (
            "domain_onboarding",
            r"^\s*(?:请)?(?:切换到|切换为|切换|进入|开始)"
            r"(?:领域入门模式|领域入门|入门模式)"
            r"\s*[，,：:\s]*(.*)$",
        ),
        (
            "chat",
            r"^\s*(?:请)?(?:切回|切换到|切换为|切换)"
            r"(?:普通聊天模式|普通聊天|聊天模式|聊天)"
            r"\s*[，,：:\s]*(.*)$",
        ),
    )

    for mode, pattern in patterns:
        match = re.match(pattern, raw, re.IGNORECASE)
        if match:
            return mode, match.group(1).strip()

    normalized = raw.replace(" ", "")

    if normalized in {
        "退出论文精读",
        "退出论文精读模式",
        "退出领域入门",
        "退出领域入门模式",
        "退出入门模式",
    }:
        return "chat", ""

    return None, ""


def detect_mode_switch(text: str) -> str | None:
    """兼容旧调用方式，只返回模式。"""
    mode, _ = parse_mode_switch(text)
    return mode


def mode_switch_reply(mode: str) -> str:
    if mode == "paper_reading":
        return (
            "已切换到论文精读模式。\n"
            "请发送 PDF 文件，或发送论文链接。"
        )

    if mode == "domain_onboarding":
        return (
            "已切换到领域入门模式。\n"
            "请告诉我你想入门的研究领域。"
        )

    return "已切换到普通聊天模式。"


def handle_feishu_mode_message(
    message: ChannelMessage,
    app_state: Any,
    *,
    mode: str,
    download_feishu_file: Callable[[str, str], bytes],
) -> dict[str, Any]:
    """根据当前飞书会话模式调用 NoviceSynapse 官方 handler。"""

    if mode == "chat":
        message.mode = "chat"
        return handle_chat_message(
            message,
            app_state,
        )

    if mode == "domain_onboarding":
        message.mode = "domain_onboarding"

        result = handle_domain_onboarding_message(
            message,
            app_state,
        )

        # 这里只影响飞书端输出。
        # Web 端仍然直接使用原来的 domain onboarding handler。
        if isinstance(result, dict):
            for key in (
                "text",
                "answer",
                "message",
                "content",
                "summary",
                "response",
                "agent_response",
            ):
                value = result.get(key)

                if isinstance(value, str) and value.strip():
                    return {
                        "text": (f"{value.strip()}\n\n"
                                 "详细「入门路线」请在网页端查看：\n"
                                 "http://127.0.0.1:8000/library"
                                 ),
                        "status": result.get("status", "ok"),
                    }

            data = result.get("data")

            if isinstance(data, dict):
                for key in (
                    "text",
                    "answer",
                    "message",
                    "content",
                    "summary",
                    "response",
                    "agent_response",
                ):
                    value = data.get(key)

                    if isinstance(value, str) and value.strip():
                        return {
                            "text": value.strip(),
                            "status": result.get("status", "ok"),
                        }

            # 没有适合直接在飞书展示的文本时，
            # 不再把内部 JSON 原样发给用户。
            return {
                "text": "请前往网页端（http://127.0.0.1:8000）查看详细「入门路线」。",
                "status": "ok",
            }

        return {
            "text": str(result),
            "status": "ok",
        }

    if mode == "paper_reading":
        return _handle_paper_reading(
            message,
            app_state,
            download_feishu_file=download_feishu_file,
        )

    return {
        "text": f"未知模式：{mode}",
        "status": "error",
    }


def _handle_paper_reading(
    message: ChannelMessage,
    app_state: Any,
    *,
    download_feishu_file: Callable[[str, str], bytes],
) -> dict[str, Any]:
    """处理飞书论文精读入口。"""

    message.mode = "paper_reading"

    pdf_bytes: bytes | None = None
    original_filename = ""
    source_url = ""

    if isinstance(message.content, dict):
        file_key = str(
            message.content.get("file_key") or ""
        )
        original_filename = str(
            message.content.get("file_name") or ""
        )

        if not file_key:
            return {
                "text": "没有读取到飞书文件信息，请重新发送 PDF。",
                "status": "error",
            }

        if (
            original_filename
            and not original_filename.lower().endswith(".pdf")
        ):
            return {
                "text": "论文精读目前只支持 PDF 文件。",
                "status": "error",
            }

        message_id = str(
            message.metadata.get("feishu_message_id") or ""
        )

        try:
            pdf_bytes = download_feishu_file(
                message_id,
                file_key,
            )
        except Exception as error:
            return {
                "text": f"下载飞书 PDF 失败：{error}",
                "status": "error",
            }

    else:
        text = str(message.content or "").strip()

        match = _PAPER_URL_RE.search(text)

        if not match:
            return {
                "text": (
                    "当前处于论文精读模式。\n"
                    "请先发送 PDF 文件或论文链接。"
                ),
                "status": "ok",
            }

        source_url = match.group(0).rstrip(
            "。，,；;）)]}"
        )

        try:
            pdf_bytes = download_pdf_bytes(
                source_url
            )
        except Exception as error:
            return {
                "text": f"下载论文链接失败：{error}",
                "status": "error",
            }

    if not pdf_bytes:
        return {
            "text": "没有获取到有效 PDF 数据。",
            "status": "error",
        }

    if not pdf_bytes.startswith(b"%PDF-"):
        return {
            "text": "获取到的文件不是有效 PDF。",
            "status": "error",
        }

    encoded = base64.b64encode(
        pdf_bytes
    ).decode("ascii")

    upload_payload: dict[str, Any] = {
        "action": "upload_paper",
        "pdf_data": encoded,
        "metadata": {
            "original_filename": original_filename,
        },
    }

    if source_url:
        upload_payload["pdf_url"] = source_url

    upload_message = _paper_message(
        message,
        upload_payload,
    )

    upload_result = handle_paper_reading_message(
        upload_message,
        app_state,
    )

    if upload_result.get("status") != "ok":
        return _paper_error(
            "论文导入失败",
            upload_result,
        )

    paper_id = str(
        (upload_result.get("data") or {}).get("paper_id")
        or ""
    )

    if not paper_id:
        return {
            "text": "论文导入成功，但没有获得 paper_id。",
            "status": "error",
        }

    parse_error = _wait_for_parse(
        app_state,
        paper_id,
    )

    if parse_error:
        return {
            "text": parse_error,
            "status": "error",
        }

    create_message = _paper_message(
        message,
        {
            "action": "create_session",
            "paper_id": paper_id,
            "conversation_id": message.session_id,
        },
    )

    create_result = handle_paper_reading_message(
        create_message,
        app_state,
    )

    if create_result.get("status") != "ok":
        return _paper_error(
            "创建论文精读会话失败",
            create_result,
        )

    reading_session_id = str(
        (create_result.get("data") or {}).get("session_id")
        or (create_result.get("session") or {}).get("session_id")
        or ""
    )

    if not reading_session_id:
        return {
            "text": "论文已导入，但创建阅读会话失败。",
            "status": "error",
        }

    start_message = _paper_message(
        message,
        {
            "action": "start_reading",
            "session_id": reading_session_id,
            "paper_id": paper_id,
            "content": (
                "请开始精读这篇论文，"
                "并先给出论文整体导读。"
            ),
        },
    )

    start_result = handle_paper_reading_message(
        start_message,
        app_state,
    )

    if start_result.get("status") != "ok":
        return _paper_error(
            "开始论文精读失败",
            start_result,
        )

    data = start_result.get("data") or {}

    answer = str(
        data.get("agent_response") or ""
    ).strip()

    if not answer:
        answer = (
            "论文已成功导入，并创建了论文精读会话。"
        )

    return {
        "text": answer,
        "status": "ok",
        "paper_id": paper_id,
        "paper_reading_session_id": reading_session_id,
    }


def _paper_message(
    source: ChannelMessage,
    content: dict[str, Any],
) -> ChannelMessage:
    """创建供论文精读 handler 使用的内部消息。"""

    return ChannelMessage(
        session_id=source.session_id,
        channel=source.channel,
        direction=source.direction,
        mode="paper_reading",
        content=content,
        user_id=source.user_id,
        metadata=dict(source.metadata),
    )


def _wait_for_parse(
    app_state: Any,
    paper_id: str,
    timeout_seconds: float = 120.0,
) -> str:
    """等待后台 PDF 解析完成。"""

    storage = getattr(
        app_state,
        "paper_storage",
        None,
    )

    if storage is None:
        return "Paper storage 未初始化。"

    deadline = (
        time.monotonic()
        + timeout_seconds
    )

    while time.monotonic() < deadline:
        paper = (
            storage.load_paper(paper_id)
            or {}
        )

        status = str(
            paper.get("parse_status") or ""
        )

        if status == "done":
            return ""

        if status == "failed":
            error = str(
                paper.get("parse_error")
                or "未知错误"
            )

            return f"PDF 解析失败：{error}"

        time.sleep(0.5)

    return (
        "PDF 已上传，但解析时间较长，"
        "请稍后再试。"
    )


def _paper_error(
    prefix: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """统一论文精读错误输出。"""

    detail = str(
        result.get("message")
        or result.get("error")
        or "未知错误"
    )

    return {
        "text": f"{prefix}：{detail}",
        "status": "error",
    }