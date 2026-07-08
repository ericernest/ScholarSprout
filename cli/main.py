"""提供 NoviceSynapse 的 CLI 主入口。"""

from __future__ import annotations

import typer

from config.app import run_config_flow
from doctor.app import run_doctor_check

app = typer.Typer(
    name="novicesynapse",
    help="NoviceSynapse - AI Research Assistant\n初次运行或需修改模型请运行 config 命令。",
    no_args_is_help=True,
)


# 执行交互式配置流程。
@app.command()
def config() -> None:
    run_config_flow()


# 启动本地 FastAPI gateway。
@app.command()
def gateway(
    host: str = typer.Option("127.0.0.1", help="Gateway host."),
    port: int = typer.Option(8000, help="Gateway port."),
    open_browser: bool = typer.Option(False, "--open-browser", help="Open the gateway URL in a browser."),
) -> None:
    try:
        from gateway.app import start_gateway_server
    except ModuleNotFoundError as error:
        typer.echo("Gateway dependencies are not installed. Please install project dependencies first.")
        raise typer.Exit(code=1) from error

    if open_browser:
        import threading
        import webbrowser

        browser_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
        timer = threading.Timer(1.0, webbrowser.open, args=(f"http://{browser_host}:{port}",))
        timer.daemon = True
        timer.start()

    start_gateway_server(host=host, port=port)


# 检查当前配置是否可用。
@app.command()
def doctor() -> None:
    run_doctor_check()


# 预留 agent 命令入口。
@app.command()
def agent() -> None:
    typer.echo("Agent command is not implemented yet.")


if __name__ == "__main__":
    app()