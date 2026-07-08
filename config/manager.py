"""提供 NoviceSynapse 的配置加载与保存能力。"""

from __future__ import annotations

import json
from pathlib import Path

from .schema import AppConfig, OpenAIClientConfig, dump_app_config

USER_CONFIG_DIR = Path.home() / ".novicesynapse"
USER_CONFIG_FILE = USER_CONFIG_DIR / "config.json"


# 读取应用配置。
def load_config() -> AppConfig:
    if not USER_CONFIG_FILE.exists():
        return AppConfig()

    try:
        data = json.loads(USER_CONFIG_FILE.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Failed to parse config file: {USER_CONFIG_FILE}") from error
    except OSError as error:
        raise RuntimeError(f"Failed to read config file: {USER_CONFIG_FILE}") from error

    client_data = data.get("client", {})

    return AppConfig(
        client=OpenAIClientConfig(
            api_key=client_data.get("api_key", ""),
            base_url=client_data.get("base_url") or None,
            model_name=client_data.get("model_name", "gpt-4o-mini"),
            timeout=client_data.get("timeout", 60.0),
            max_retries=client_data.get("max_retries", 2),
        )
    )


# 保存应用配置。
def save_config(config: AppConfig) -> Path:
    try:
        USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        USER_CONFIG_FILE.write_text(
            json.dumps(dump_app_config(config), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as error:
        raise RuntimeError(f"Failed to write config file: {USER_CONFIG_FILE}") from error

    return USER_CONFIG_FILE
