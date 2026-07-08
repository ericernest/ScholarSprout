"""定义 NoviceSynapse 的配置数据结构。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


# 描述 OpenAI client 的连接配置。
@dataclass(slots=True)
class OpenAIClientConfig:
    api_key: str = ""
    base_url: str | None = None
    model_name: str = "gpt-4o-mini"
    timeout: float = 60.0
    max_retries: int = 2


# 描述当前应用配置。
@dataclass(slots=True)
class AppConfig:
    client: OpenAIClientConfig = field(default_factory=OpenAIClientConfig)


# 将配置对象转换为可写入 JSON 的字典。
def dump_app_config(config: AppConfig) -> dict[str, Any]:
    return asdict(config)
