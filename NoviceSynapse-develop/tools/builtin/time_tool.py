"""实现获取当前时间的内置工具。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tools.base import BaseTool, ToolSpec


# 返回当前本地时间或指定时区时间。
class CurrentTimeTool(BaseTool):
    # 初始化工具说明。
    def __init__(self) -> None:
        self.spec = ToolSpec(
            name="get_current_time",
            description="Get the current local time.",
            parameters={
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": "Optional timezone name, such as Asia/Shanghai or America/Los_Angeles.",
                    }
                },
                "required": [],
            },
        )

    # 执行当前时间查询。
    def run(self, arguments: dict[str, Any]) -> dict[str, str]:
        timezone_name = str(arguments.get("timezone") or "").strip()

        if timezone_name:
            try:
                now = datetime.now(ZoneInfo(timezone_name))
                return self._format_time(timezone_name, now)
            except ZoneInfoNotFoundError:
                now = datetime.now().astimezone()
                result = self._format_time("local", now)
                result["error"] = f"Invalid timezone: {timezone_name}"
                return result

        now = datetime.now().astimezone()
        return self._format_time("local", now)

    # 格式化时间结果。
    def _format_time(self, timezone_name: str, current_time: datetime) -> dict[str, str]:
        return {
            "timezone": timezone_name,
            "current_time": current_time.strftime("%Y-%m-%d %H:%M:%S"),
            "iso": current_time.isoformat(),
        }
