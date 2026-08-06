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
        max_tokens: int | None = None,
        timeout: float | None = None,
        response_format: dict[str, Any] | None = None,
        model_name: str | None = None,
    ) -> Any:
        kwargs = self._chat_kwargs(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            max_tokens=max_tokens,
            response_format=response_format,
            model_name=model_name,
        )
        client = (
            self.client.with_options(timeout=timeout, max_retries=0)
            if timeout is not None
            else self.client
        )
        return client.chat.completions.create(**kwargs)

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        response_format: dict[str, Any] | None = None,
        model_name: str | None = None,
    ) -> Any:
        """Yield native Chat Completions chunks from OpenAI-compatible providers."""
        kwargs = self._chat_kwargs(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            max_tokens=max_tokens,
            response_format=response_format,
            model_name=model_name,
        )
        kwargs["stream"] = True
        client = (
            self.client.with_options(timeout=timeout, max_retries=0)
            if timeout is not None
            else self.client
        )
        return client.chat.completions.create(**kwargs)

    def _chat_kwargs(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict] | None,
        tool_choice: str | None,
        max_tokens: int | None,
        response_format: dict[str, Any] | None,
        model_name: str | None,
    ) -> dict[str, Any]:
        selected_model = (model_name or self.config.model_name).strip()
        if not selected_model:
            raise ValueError("model_name is empty. Please run config and choose a model.")

        kwargs: dict[str, Any] = {
            "model": selected_model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if response_format is not None:
            kwargs["response_format"] = response_format

        return kwargs

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        """Create dense vectors through an OpenAI-compatible embedding endpoint."""
        if not texts:
            return []
        response = self.client.embeddings.create(model=model, input=texts)
        ordered = sorted(response.data, key=lambda item: item.index)
        return [list(item.embedding) for item in ordered]
