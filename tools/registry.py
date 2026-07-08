"""提供工具注册表。"""

from __future__ import annotations

from .base import BaseTool
from .builtin.time_tool import CurrentTimeTool


# 管理当前可用工具实例。
class ToolRegistry:
    # 初始化空工具注册表。
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    # 注册一个工具。
    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    # 根据名称获取工具。
    def get(self, name: str) -> BaseTool:
        return self._tools[name]

    # 列出全部已注册工具。
    def list_tools(self) -> list[BaseTool]:
        return list(self._tools.values())

    # 根据 agent 允许列表获取工具。
    def get_allowed_tools(self, tool_names: list[str]) -> list[BaseTool]:
        return [self.get(tool_name) for tool_name in tool_names]

    # 转换为 OpenAI tools schema。
    def to_openai_tools(self, tool_names: list[str]) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.spec.name,
                    "description": tool.spec.description,
                    "parameters": tool.spec.parameters,
                },
            }
            for tool in self.get_allowed_tools(tool_names)
        ]


# 创建内置工具注册表。
def create_builtin_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(CurrentTimeTool())
    return registry
