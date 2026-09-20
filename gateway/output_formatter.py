"""
统一处理 Channel 输出格式。

负责：
1. Markdown -> 聊天平台文本
2. domain onboarding URL策略
3. dict结果提取
"""

from __future__ import annotations

import os
import re
from typing import Any


def extract_text(content: Any) -> str:
    """
    从 handler 输出中提取文本。
    """

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, dict):

        for key in (
            "text",
            "content",
            "answer",
            "message",
            "summary",
            "response",
            "agent_response",
        ):
            value = content.get(key)

            if isinstance(value, str) and value.strip():
                return value.strip()

        data = content.get("data")

        if isinstance(data, dict):

            for key in (
                "text",
                "content",
                "answer",
                "message",
            ):
                value = data.get(key)

                if isinstance(value, str):
                    return value.strip()

        return str(content)

    return str(content or "")


def markdown_to_text(text: str) -> str:
    """
    简单 Markdown 清理。

    QQ/飞书均不适合直接展示Markdown。
    """

    text = re.sub(
        r"```.*?```",
        lambda m: m.group(0)
        .replace("```", ""),
        text,
        flags=re.S,
    )

    text = re.sub(
        r"^#{1,6}\s*",
        "",
        text,
        flags=re.MULTILINE,
    )

    text = text.replace("**", "")
    text = text.replace("__", "")

    text = re.sub(
        r"\[([^\]]+)\]\([^)]+\)",
        r"\1",
        text,
    )

    text = re.sub(
        r"^\s*[-*]\s+",
        "• ",
        text,
        flags=re.MULTILINE,
    )

    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text,
    )

    return text.strip()


def append_library_url(
    text: str,
    mode: str,
) -> str:
    """
    根据部署环境决定是否暴露资料库地址。
    """

    if mode != "domain_onboarding":
        return text

    url = os.getenv(
        "NOVICESYNAPSE_PUBLIC_LIBRARY_URL",
        "",
    ).strip()

    if not url:
        return text

    if url in text:
        return text

    return (
        f"{text}\n\n"
        "详细「入门路线」请在网页端查看：\n"
        f"{url}"
    )


def format_channel_output(
    content: Any,
    *,
    mode: str,
) -> str:

    text = extract_text(content)

    text = markdown_to_text(text)

    text = append_library_url(
        text,
        mode,
    )

    return text