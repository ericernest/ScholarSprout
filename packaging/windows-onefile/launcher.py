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
from typing import Any

from gateway.app import start_gateway_server


HOST = "127.0.0.1"
PREFERRED_PORT = 8000
MAX_PORT = 8099


def _icon_path() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "packaging" / "windows-onefile" / "seefurther.ico"
    return Path(__file__).with_name("seefurther.ico")


class _TrayController:
    def __init__(self, port: int) -> None:
        self.url = f"http://{HOST}:{port}/"
        self._lock = threading.Lock()
        self._server: Any | None = None
        self._icon: Any | None = None
        self._stop_requested = False

    def set_server(self, server: Any) -> None:
        with self._lock:
            self._server = server
            if self._stop_requested:
                server.should_exit = True

    def set_icon(self, icon: Any) -> None:
        with self._lock:
            self._icon = icon

    def open_app(self, _icon: Any = None, _item: Any = None) -> None:
        webbrowser.open(self.url)

    def exit_app(self, icon: Any = None, _item: Any = None) -> None:
        with self._lock:
            self._stop_requested = True
            server = self._server
            tray_icon = icon or self._icon
        if server is not None:
            server.should_exit = True
        if tray_icon is not None:
            tray_icon.stop()

    def stop_tray(self) -> None:
        with self._lock:
            tray_icon = self._icon
        if tray_icon is not None:
            tray_icon.stop()


def _start_tray(controller: _TrayController) -> threading.Thread:
    import pystray
    from PIL import Image

    with Image.open(_icon_path()) as icon_source:
        tray_image = icon_source.convert("RGBA")
    tray_icon = pystray.Icon(
        "SeeFurther",
        tray_image,
        "研见 · SeeFurther",
        menu=pystray.Menu(
            pystray.MenuItem("打开研见", controller.open_app, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出研见", controller.exit_app),
        ),
    )
    controller.set_icon(tray_icon)
    tray_thread = threading.Thread(
        target=tray_icon.run,
        name="seefurther-tray",
        daemon=True,
    )
    tray_thread.start()
    return tray_thread


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
        tray_controller = _TrayController(port)
        if os.getenv("SEEFURTHER_SKIP_TRAY") != "1":
            _start_tray(tray_controller)
        if os.getenv("SEEFURTHER_SKIP_BROWSER") != "1":
            threading.Thread(target=_open_when_ready, args=(port,), daemon=True).start()
        start_gateway_server(
            host=HOST,
            port=port,
            on_server_created=tray_controller.set_server,
        )
    except Exception as exc:
        _show_error(str(exc))
        raise
    finally:
        if "tray_controller" in locals():
            tray_controller.stop_tray()


if __name__ == "__main__":
    main()
