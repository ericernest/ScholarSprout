"""Windows one-file launcher for SeeFurther."""

from __future__ import annotations

import ctypes
import multiprocessing
import os
import socket
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path

from gateway.app import start_gateway_server


HOST = "127.0.0.1"
PREFERRED_PORT = 8000
MAX_PORT = 8099


def _find_available_port() -> int:
    for port in range(PREFERRED_PORT, MAX_PORT + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((HOST, port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"{PREFERRED_PORT}-{MAX_PORT} 端口均被占用。")


def _open_when_ready(port: int) -> None:
    health_url = f"http://{HOST}:{port}/health"
    for _ in range(240):
        try:
            with urllib.request.urlopen(health_url, timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(f"http://{HOST}:{port}/")
                    return
        except Exception:
            time.sleep(0.25)


def _show_error(message: str) -> None:
    log_root = Path(os.getenv("LOCALAPPDATA") or Path.home()) / "SeeFurther"
    try:
        log_root.mkdir(parents=True, exist_ok=True)
        (log_root / "launcher-error.log").write_text(
            f"{message}\n\n{traceback.format_exc()}",
            encoding="utf-8",
        )
    except OSError:
        pass
    ctypes.windll.user32.MessageBoxW(
        None,
        message,
        "研见 · SeeFurther 启动失败",
        0x10,
    )


def main() -> None:
    multiprocessing.freeze_support()
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    try:
        port = _find_available_port()
        if os.getenv("SEEFURTHER_SKIP_BROWSER") != "1":
            threading.Thread(target=_open_when_ready, args=(port,), daemon=True).start()
        start_gateway_server(host=HOST, port=port)
    except Exception as exc:
        _show_error(str(exc))
        raise


if __name__ == "__main__":
    main()
