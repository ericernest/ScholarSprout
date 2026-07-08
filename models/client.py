"""提供 NoviceSynapse 的 OpenAI client 封装。"""

from __future__ import annotations

from typing import Any

from openai import OpenAI

from config.schema import OpenAIClientConfig


# 只负责根据连接配置声明 OpenAI SDK client。
class OpenAIClient:
    # 初始化 OpenAI SDK client。
    def __init__(self, config: OpenAIClientConfig):
        self.config = config
        self.client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout,
            max_retries=config.max_retries,
        )

    # 调用 OpenAI chat completions。
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": getattr(self.config, "model_name", "gpt-4o-mini"),
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice

        return self.client.chat.completions.create(**kwargs)
