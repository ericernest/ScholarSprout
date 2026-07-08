"""定义 NoviceSynapse 的配置命令交互流程。"""

from __future__ import annotations

import typer

from .manager import load_config, save_config


# 执行交互式模型配置流程。
def run_config_flow() -> None:
    try:
        config = load_config()
    except RuntimeError as error:
        typer.echo(str(error))
        raise typer.Exit(code=1) from error

    should_modify = typer.prompt(
        "修改模型（不修改请输入 no，是的话输入 yes）",
        default="no",
    ).strip().lower()

    if should_modify != "yes":
        typer.echo("未修改模型配置。")
        return

    base_url = typer.prompt(
        "请输入 base_url",
        default=config.client.base_url or "",
    ).strip()
    api_key = typer.prompt(
        "请输入 api-key",
        default=config.client.api_key,
    ).strip()

    config.client.base_url = base_url or None
    config.client.api_key = api_key

    try:
        save_config(config)
    except RuntimeError as error:
        typer.echo(str(error))
        raise typer.Exit(code=1) from error

    typer.echo("模型修改成功，配置文件已更新。")