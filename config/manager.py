"""提供 NoviceSynapse 的配置加载与保存能力。"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .schema import (
    AppConfig,
    ChannelsConfig,
    EmbeddingConfig,
    FeishuConfig,
    OpenAIClientConfig,
    StorageConfig,
    dump_app_config,
)

USER_CONFIG_DIR = Path.home() / ".scholarsprout"
USER_CONFIG_FILE = USER_CONFIG_DIR / "config.json"
DEFAULT_DATA_DIR = "~/.scholarsprout"


def get_config_file() -> Path:
    """Return the configured config-file location (mainly useful for packaging/tests)."""
    override = os.getenv("NOVICESYNAPSE_CONFIG_FILE")
    return Path(override).expanduser() if override else USER_CONFIG_FILE


# 读取应用配置。
def load_config(config_file: Path | None = None) -> AppConfig:
    path = Path(config_file) if config_file is not None else get_config_file()
    if not path.exists():
        return AppConfig()

    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Failed to parse config file: {path}") from error
    except OSError as error:
        raise RuntimeError(f"Failed to read config file: {path}") from error

    client_data = data.get("client", {})
    embedding_data = data.get("embedding", {})
    storage_data = data.get("storage", {})
    channels_data = data.get("channels", {})
    feishu_data = channels_data.get("feishu", {})
    return AppConfig(
        client=OpenAIClientConfig(
            api_key=client_data.get("api_key", ""),
            base_url=client_data.get("base_url") or None,
            model_name=client_data.get("model_name", ""),
            timeout=client_data.get("timeout", 60.0),
            max_retries=client_data.get("max_retries", 2),
            input_cost_per_million_tokens=client_data.get(
                "input_cost_per_million_tokens"
            ),
            output_cost_per_million_tokens=client_data.get(
                "output_cost_per_million_tokens"
            ),
        ),
        embedding=EmbeddingConfig(
            model_name=str(
                embedding_data.get("model_name") or "qwen3-embedding"
            ).strip(),
            base_url=embedding_data.get("base_url") or None,
            api_key=str(embedding_data.get("api_key") or ""),
        ),
        storage=StorageConfig(
            data_dir=str(storage_data.get("data_dir") or DEFAULT_DATA_DIR),
        ),
        channels=ChannelsConfig(
            feishu=FeishuConfig(
                enabled=bool(feishu_data.get("enabled", False)),
                app_id=str(feishu_data.get("app_id") or "").strip(),
                app_secret=str(feishu_data.get("app_secret") or "").strip(),
            ),
        ),
    )


# 保存应用配置。
def save_config(config: AppConfig, config_file: Path | None = None) -> Path:
    path = Path(config_file) if config_file is not None else get_config_file()
    directory = path.parent
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        directory.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(dump_app_config(config), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
    except OSError as error:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError(f"Failed to write config file: {path}") from error

    return path


def resolve_data_dir(config: AppConfig) -> Path:
    """Resolve the effective data directory, preserving the deployment override."""
    configured = os.getenv("NOVICESYNAPSE_DATA_DIR") or config.storage.data_dir
    return Path(configured or DEFAULT_DATA_DIR).expanduser().resolve()


def is_setup_complete(config: AppConfig) -> bool:
    return bool(config.client.api_key.strip() and config.client.model_name.strip())
