from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_linux_package_is_self_contained_and_smoke_tested() -> None:
    build = (ROOT / "packaging" / "linux-x64" / "build.sh").read_text(encoding="utf-8")
    launcher = (ROOT / "packaging" / "linux-x64" / "launcher.py").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "build-linux-x64.yml").read_text(encoding="utf-8")

    assert "--onedir" in build
    assert "gateway/static:gateway/static" in build
    assert "lark_oapi" in build
    assert "start_gateway_server(host=HOST, port=port)" in launcher
    assert "SCHOLARSPROUT_SKIP_BROWSER" in launcher
    assert "rockylinux:8" in workflow
    assert "python3.11-devel" in workflow
    assert "Smoke test packaged gateway" in workflow
    assert "actions/upload-artifact@v4" in workflow
