"""提供 NoviceSynapse 的 OpenAI client 封装。"""

from __future__ import annotations

from typing import Any

import httpx
from openai import OpenAI

from config.schema import OpenAIClientConfig


class SetupRequiredModel:
    """Keep the web service available until first-run model setup is complete."""

    def __init__(self, config: OpenAIClientConfig) -> None:
        self.config = config

    def _raise(self) -> None:
        raise RuntimeError("模型尚未配置，请先打开 /settings 完成配置并重启服务。")

    def chat(self, **_: Any) -> Any:
        self._raise()

    def chat_stream(self, **_: Any) -> Any:
        self._raise()

    def embed(self, *_: Any, **__: Any) -> Any:
        self._raise()


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
        max_retries: int | None = None,
        response_format: dict[str, Any] | None = None,
        model_name: str | None = None,
        disable_thinking: bool = False,
    ) -> Any:
        kwargs = self._chat_kwargs(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            max_tokens=max_tokens,
            response_format=response_format,
            model_name=model_name,
            disable_thinking=disable_thinking,
        )
        if timeout is not None:
            client = self.client.with_options(
                timeout=timeout,
                max_retries=0 if max_retries is None else max_retries,
            )
        elif max_retries is not None:
            client = self.client.with_options(max_retries=max_retries)
        else:
            client = self.client
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
        disable_thinking: bool = False,
    ) -> Any:
        """Yield native Chat Completions chunks from OpenAI-compatible providers."""
        kwargs = self._chat_kwargs(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            max_tokens=max_tokens,
            response_format=response_format,
            model_name=model_name,
            disable_thinking=disable_thinking,
        )
        kwargs["stream"] = True
        # OpenAI-compatible providers only attach token usage to the terminal
        # streaming chunk when explicitly requested. Without this option the
        # domain-onboarding audit sees successful calls with zero tokens.
        kwargs["stream_options"] = {"include_usage": True}
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
        disable_thinking: bool,
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
        if disable_thinking and selected_model.lower().startswith("deepseek-v4-"):
            # DeepSeek V4 enables thinking by default. Structured JSON calls
            # should spend their output budget on the requested object rather
            # than an internal reasoning trace.
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

        return kwargs

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        """Create dense vectors through an OpenAI-compatible embedding endpoint."""
        if not texts:
            return []
        response = self.client.embeddings.create(model=model, input=texts)
        ordered = sorted(response.data, key=lambda item: item.index)
        return [list(item.embedding) for item in ordered]
