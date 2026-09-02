import importlib.util
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "packaging" / "windows-onefile"


def test_onefile_packaging_sources_are_complete() -> None:
    launcher = (PACKAGE_ROOT / "launcher.py").read_text(encoding="utf-8")
    build_script = (PACKAGE_ROOT / "build.ps1").read_text(encoding="utf-8")
    icon_bytes = (PACKAGE_ROOT / "scholarsprout.ico").read_bytes()

    assert "start_gateway_server" in launcher
    assert "SCHOLARSPROUT_SKIP_BROWSER" in launcher
    assert 'ValidateSet("OneDir", "OneFile", "Both")' in build_script
    assert '[string]$Mode = "OneDir"' in build_script
    assert '[string]$BuildPythonPath = ""' in build_script
    assert '[string]$BuildRootPath = ""' in build_script
    assert '"--onedir"' in build_script
    assert '"--onefile"' in build_script
    assert '"$StaticDir;gateway\\static"' in build_script
    assert '"$BuiltinSkillsDir;skills\\builtin"' in build_script
    assert '"$AgentProfiles;agents"' in build_script
    assert '"--collect-all", "fitz"' in build_script
    assert 'if ($LASTEXITCODE -ne 0)' in build_script
    assert "PyInstaller 打包失败" in build_script
    assert '"Pillow>=10,<13"' in build_script
    assert '"pystray>=0.19,<1"' in build_script
    assert "import PyInstaller, PIL, pystray, lark_oapi" in build_script
    assert '"--collect-all", "lark_oapi"' in build_script
    assert '"--icon", $AppIcon' in build_script
    assert '"--hidden-import", "pystray._win32"' in build_script
    assert '$AppName = "ScholarSprout"' in build_script
    assert '"$AppName-windows-x64-portable.zip"' in build_script
    assert '"$AppName-$ReleaseVersion-windows-x64.exe"' in build_script
    assert 'Compress-Archive -LiteralPath $OneDirPath' in build_script
    assert icon_bytes[:4] == b"\x00\x00\x01\x00"
    assert int.from_bytes(icon_bytes[4:6], "little") >= 8


def test_tray_menu_uses_brand_actions_and_graceful_shutdown() -> None:
    launcher = (PACKAGE_ROOT / "launcher.py").read_text(encoding="utf-8")
    gateway = (REPO_ROOT / "gateway" / "app.py").read_text(encoding="utf-8")

    assert 'pystray.MenuItem("打开科研萌芽"' in launcher
    assert 'pystray.MenuItem("退出科研萌芽"' in launcher
    assert "server.should_exit = True" in launcher
    assert "on_server_created=tray_controller.set_server" in launcher
    assert "on_server_created: Callable[[uvicorn.Server], None] | None" in gateway


def test_tray_exit_requests_server_shutdown_and_stops_icon() -> None:
    launcher_path = PACKAGE_ROOT / "launcher.py"
    spec = importlib.util.spec_from_file_location("scholarsprout_windows_launcher", launcher_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    controller = module._TrayController(8000)
    server = SimpleNamespace(should_exit=False)
    tray_icon = SimpleNamespace(stopped=False)
    tray_icon.stop = lambda: setattr(tray_icon, "stopped", True)
    controller.set_server(server)
    controller.set_icon(tray_icon)

    controller.exit_app()

    assert server.should_exit is True
    assert tray_icon.stopped is True


def test_onefile_local_artifacts_are_ignored() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "packaging/windows-onefile/output/" in gitignore
    assert "packaging/windows-onefile/work/" in gitignore
    assert "packaging/windows-onefile/.venv/" in gitignore
