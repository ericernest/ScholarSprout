"""提供轻量单 agent 执行器。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from time import perf_counter
from typing import Any


@dataclass(slots=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reported: bool = False

    def add(self, other: "TokenUsage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens
        self.reported = self.reported or other.reported


@dataclass(slots=True)
class AgentRunResult:
    text: str
    duration_ms: float
    usage: TokenUsage
    model_calls: int


# 从对象或字典中读取字段。
def get_value(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


# 从模型响应中读取 assistant message。
def get_response_message(response: Any) -> Any:
    choices = get_value(response, "choices", [])
    if not choices:
        return {}
    return get_value(choices[0], "message", {})


# 从 assistant message 中读取文本内容。
def get_message_content(message: Any) -> str:
    return str(get_value(message, "content", "") or "")


def get_response_usage(response: Any) -> TokenUsage:
    raw_usage = get_value(response, "usage", None)
    usage = raw_usage or {}
    prompt_tokens = int(
        get_value(usage, "prompt_tokens", get_value(usage, "input_tokens", 0)) or 0
    )
    completion_tokens = int(
        get_value(
            usage,
            "completion_tokens",
            get_value(usage, "output_tokens", 0),
        )
        or 0
    )
    total_tokens = int(
        get_value(usage, "total_tokens", prompt_tokens + completion_tokens)
        or prompt_tokens + completion_tokens
    )
    return TokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        reported=raw_usage is not None,
    )


# 从 assistant message 中读取 tool calls。
def get_tool_calls(message: Any) -> list[Any]:
    return list(get_value(message, "tool_calls", []) or [])


# 从 tool call 中读取工具名。
def get_tool_call_name(tool_call: Any) -> str:
    function = get_value(tool_call, "function", {})
    return str(get_value(function, "name", "") or "")


# 从 tool call 中读取 ID。
def get_tool_call_id(tool_call: Any) -> str:
    return str(get_value(tool_call, "id", "") or "")


# 从 tool call 中读取参数。
def get_tool_call_arguments(tool_call: Any) -> Any:
    function = get_value(tool_call, "function", {})
    return get_value(function, "arguments", None)


# 将 tool call 转成 OpenAI message 可接受的 dict。
def format_tool_call(tool_call: Any) -> dict[str, Any]:
    return {
        "id": get_tool_call_id(tool_call),
        "type": str(get_value(tool_call, "type", "function") or "function"),
        "function": {
            "name": get_tool_call_name(tool_call),
            "arguments": get_tool_call_arguments(tool_call) or "{}",
        },
    }


# 将 assistant message 转成可追加到 messages 的格式。
def to_assistant_message(message: Any) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": get_message_content(message) or None,
        "tool_calls": [format_tool_call(tool_call) for tool_call in get_tool_calls(message)],
    }


# 安全解析工具参数。
def parse_tool_arguments(raw_args: Any) -> dict[str, Any]:
    if raw_args is None:
        return {}
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str):
        if not raw_args.strip():
            return {}
        try:
            arguments = json.loads(raw_args)
        except json.JSONDecodeError as error:
            return {
                "_raw": raw_args,
                "_parse_error": str(error),
            }
        if isinstance(arguments, dict):
            return arguments
        return {
            "_raw": raw_args,
            "_parse_error": "Tool arguments must be a JSON object.",
        }
    return {"_raw": str(raw_args)}


# 执行一个 tool call 并返回 tool message。
def run_tool_call(tool_call: Any, tool_registry: Any) -> dict[str, str]:
    tool_name = get_tool_call_name(tool_call)
    arguments = parse_tool_arguments(get_tool_call_arguments(tool_call))

    try:
        tool = tool_registry.get(tool_name)
        tool_result = tool.run(arguments)
    except KeyError:
        tool_result = {"error": f"Tool not found: {tool_name}"}
    except Exception as error:
        tool_result = {"error": f"Tool execution failed: {error}"}

    return {
        "role": "tool",
        "tool_call_id": get_tool_call_id(tool_call),
        "content": json.dumps(tool_result, ensure_ascii=False),
    }


# 执行一次 agent，并返回文本、耗时与模型 token usage。
def run_agent_detailed(
    agent: Any,
    user_content: str,
    model: Any,
    tool_registry: Any,
    max_steps: int = 3,
) -> AgentRunResult:
    started_at = perf_counter()
    usage = TokenUsage()
    model_calls = 0

    def build_result(text: str) -> AgentRunResult:
        return AgentRunResult(
            text=text,
            duration_ms=round((perf_counter() - started_at) * 1000, 3),
            usage=usage,
            model_calls=model_calls,
        )

    tool_schemas = tool_registry.to_openai_tools(agent.profile.tools)
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": agent.profile.system_prompt,
        },
        {
            "role": "user",
            "content": user_content,
        },
    ]

    for _ in range(max_steps):
        model_calls += 1
        try:
            if tool_schemas:
                response = model.chat(
                    messages=messages,
                    tools=tool_schemas,
                    tool_choice="auto",
                )
            else:
                response = model.chat(messages=messages)
        except Exception as error:
            return build_result(f"LLM 调用失败：{error}")

        usage.add(get_response_usage(response))
        assistant_message = get_response_message(response)
        tool_calls = get_tool_calls(assistant_message)

        if not tool_calls:
            return build_result(get_message_content(assistant_message))

        messages.append(to_assistant_message(assistant_message))
        for tool_call in tool_calls:
            messages.append(run_tool_call(tool_call, tool_registry))

    return build_result("工具调用次数过多，已停止。")


# 保持原有调用接口，只返回 assistant 文本。
def run_agent(
    agent: Any,
    user_content: str,
    model: Any,
    tool_registry: Any,
    max_steps: int = 3,
) -> str:
    return run_agent_detailed(
        agent=agent,
        user_content=user_content,
        model=model,
        tool_registry=tool_registry,
        max_steps=max_steps,
    ).text
