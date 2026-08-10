"""Local-only web API for first-run and runtime configuration."""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator

from .manager import (
    DEFAULT_DATA_DIR,
    get_config_file,
    is_setup_complete,
    load_config,
    resolve_data_dir,
    save_config,
)

router = APIRouter(tags=["configuration"])


class ConfigUpdate(BaseModel):
    """Fields editable in the browser; an omitted API key keeps the old secret."""

    base_url: str | None = None
    api_key: str | None = None
    clear_api_key: bool = False
    model_name: str | None = None
    data_dir: str | None = None

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        normalized = (value or "").strip()
        if not normalized:
            return None
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url 必须是完整的 http(s) 地址")
        return normalized.rstrip("/")

    @field_validator("model_name")
    @classmethod
    def normalize_model_name(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("data_dir")
    @classmethod
    def validate_data_dir(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or "\x00" in normalized:
            raise ValueError("数据目录不能为空")
        expanded = Path(normalized).expanduser()
        if not expanded.is_absolute():
            raise ValueError("数据目录必须是绝对路径，也可以使用 ~/ 开头")
        return normalized


def _is_local_request(request: Request) -> bool:
    if os.getenv("NOVICESYNAPSE_ALLOW_REMOTE_CONFIG", "").strip() == "1":
        return True
    host = request.client.host if request.client is not None else ""
    if host == "testclient":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.lower() == "localhost"


def _require_local_request(request: Request) -> None:
    if not _is_local_request(request):
        raise HTTPException(
            status_code=403,
            detail="配置接口默认仅允许从运行 NoviceSynapse 的本机访问。",
        )


def _public_config(config: object) -> dict[str, object]:
    client = config.client
    storage = config.storage
    return {
        "client": {
            "base_url": client.base_url or "",
            "model_name": client.model_name,
            "api_key_configured": bool(client.api_key.strip()),
        },
        "storage": {
            "data_dir": storage.data_dir or DEFAULT_DATA_DIR,
            "effective_data_dir": str(resolve_data_dir(config)),
            "environment_override": bool(os.getenv("NOVICESYNAPSE_DATA_DIR")),
        },
        "setup_complete": is_setup_complete(config),
        "config_file": str(get_config_file()),
    }


@router.get("/api/config")
def read_web_config(request: Request) -> dict[str, object]:
    _require_local_request(request)
    try:
        return _public_config(load_config())
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


@router.put("/api/config")
def update_web_config(payload: ConfigUpdate, request: Request) -> dict[str, object]:
    _require_local_request(request)
    try:
        config = load_config()
        if "base_url" in payload.model_fields_set:
            config.client.base_url = payload.base_url
        if "model_name" in payload.model_fields_set:
            config.client.model_name = payload.model_name
        if payload.clear_api_key:
            config.client.api_key = ""
        elif payload.api_key is not None and payload.api_key.strip():
            config.client.api_key = payload.api_key.strip()
        if payload.data_dir is not None:
            target = Path(payload.data_dir).expanduser()
            if target.exists() and not target.is_dir():
                raise HTTPException(status_code=422, detail="数据目录指向了一个文件。")
            target.mkdir(parents=True, exist_ok=True)
            config.storage.data_dir = payload.data_dir
        save_config(config)
    except HTTPException:
        raise
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=422, detail=f"无法创建数据目录：{error}") from error

    return {
        **_public_config(config),
        "saved": True,
        "restart_required": True,
    }
