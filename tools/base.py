"""定义工具基础协议。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# 描述一个工具暴露给模型的结构。
@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


# 定义所有工具需要实现的最小接口。
class BaseTool:
    spec: ToolSpec

    # 返回工具稳定名称。
    @property
    def name(self) -> str:
        return self.spec.name

    # 执行工具。
    def run(self, arguments: dict[str, Any]) -> Any:
        raise NotImplementedError
