"""Linux desktop launcher for the self-contained ScholarSprout distribution."""

from __future__ import annotations

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


def _error_log_path() -> Path:
    state_home = os.getenv("XDG_STATE_HOME")
    root = Path(state_home) if state_home else Path.home() / ".local" / "state"
    return root / "ScholarSprout" / "launcher-error.log"


def _log_error(message: str) -> None:
    try:
        path = _error_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{message}\n\n{traceback.format_exc()}", encoding="utf-8")
        print(f"科研萌芽 · ScholarSprout 启动失败：{message}\n日志：{path}", file=sys.stderr)
    except OSError:
        print(f"科研萌芽 · ScholarSprout 启动失败：{message}", file=sys.stderr)


def main() -> None:
    try:
        port = _find_available_port()
        if os.getenv("SCHOLARSPROUT_SKIP_BROWSER") != "1":
            threading.Thread(target=_open_when_ready, args=(port,), daemon=True).start()
        start_gateway_server(host=HOST, port=port)
    except Exception as exc:
        _log_error(str(exc))
        raise


if __name__ == "__main__":
    main()
