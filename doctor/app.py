"""提供 ScholarSprout 的配置检查命令逻辑。"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

import typer

from config.manager import load_config
from config.schema import AppConfig


# 描述单项配置检查结果。
@dataclass(slots=True)
class CheckResult:
    name: str
    ok: bool
    message: str


# 校验 base_url 是否为有效地址。
def is_valid_base_url(base_url: str) -> bool:
    parsed_url = urlparse(base_url)
    return parsed_url.scheme in {"http", "https"} and bool(parsed_url.netloc)


# 检查 base_url 配置。
def check_base_url(config: AppConfig) -> CheckResult:
    base_url = (config.client.base_url or "").strip()

    if not base_url:
        return CheckResult("base_url", False, "base_url is empty.")

    if not is_valid_base_url(base_url):
        return CheckResult("base_url", False, "base_url must be a valid http or https URL.")

    return CheckResult("base_url", True, "base_url is valid.")


# 检查 api-key 配置。
def check_api_key(config: AppConfig) -> CheckResult:
    if not config.client.api_key.strip():
        return CheckResult("api-key", False, "api-key is empty.")

    return CheckResult("api-key", True, "api-key is set.")


# 检查 model_name 配置。
def check_model_name(config: AppConfig) -> CheckResult:
    if not config.client.model_name.strip():
        return CheckResult("model_name", False, "model_name is empty.")

    return CheckResult("model_name", True, "model_name is set.")


# 聚合执行所有配置检查。
def check_config(config: AppConfig) -> list[CheckResult]:
    checks = [check_base_url, check_api_key, check_model_name]
    return [check(config) for check in checks]


# 执行 doctor 配置检查。
def run_doctor_check() -> None:
    try:
        config = load_config()
    except RuntimeError as error:
        typer.echo(str(error))
        raise typer.Exit(code=1) from error

    results = check_config(config)
    failed_results = [result for result in results if not result.ok]

    if failed_results:
        for result in failed_results:
            typer.echo(f"Doctor failed [{result.name}]: {result.message}")
        raise typer.Exit(code=1)

    typer.echo("Doctor passed: config is valid.")
