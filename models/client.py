"""提供 NoviceSynapse 的 OpenAI client 封装。"""

from __future__ import annotations

from typing import Any

import httpx
from openai import OpenAI

from config.schema import OpenAIClientConfig


# 只负责根据连接配置声明 OpenAI SDK client。
class OpenAIClient:
    # 初始化 OpenAI SDK client。
    def __init__(self, config: OpenAIClientConfig):
        self.config = config
        self.http_client = httpx.Client(trust_env=False)
        self.client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout,
            max_retries=config.max_retries,
            http_client=self.http_client,
        )

    # 调用 OpenAI chat completions。
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
    ) -> Any:
        model_name = self.config.model_name.strip()
        if not model_name:
            raise ValueError("model_name is empty. Please run config and choose a model.")

        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice

        return self.client.chat.completions.create(**kwargs)
