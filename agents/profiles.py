"""从 JSON 文件读取 NoviceSynapse 的 agent 预设。"""

from __future__ import annotations

import json
from pathlib import Path


# 管理内置 agent 预设列表。
class Profiles:
    # 读取 agent 预设文件。
    def __init__(self, profile_path: Path | None = None):
        self.profile_path = profile_path or Path(__file__).with_name("profiles.json")
        self.profiles = self._load_profiles()

    # 加载 JSON 格式的 agent 预设列表。
    def _load_profiles(self) -> list[dict[str, object]]:
        return json.loads(self.profile_path.read_text(encoding="utf-8"))

    # 根据 agent type 获取对应预设。
    def get(self, agent_type: str) -> dict[str, object]:
        for profile in self.profiles:
            if profile.get("type") == agent_type:
                return profile

        raise ValueError(f"Unsupported agent type: {agent_type}")