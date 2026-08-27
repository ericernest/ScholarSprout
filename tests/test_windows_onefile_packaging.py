from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "packaging" / "windows-onefile"


def test_onefile_packaging_sources_are_complete() -> None:
    launcher = (PACKAGE_ROOT / "launcher.py").read_text(encoding="utf-8")
    build_script = (PACKAGE_ROOT / "build.ps1").read_text(encoding="utf-8")

    assert "start_gateway_server" in launcher
    assert "SEEFURTHER_SKIP_BROWSER" in launcher
    assert "--onefile" in build_script
    assert '"$StaticDir;gateway\\static"' in build_script
    assert '"$BuiltinSkillsDir;skills\\builtin"' in build_script
    assert '"$AgentProfiles;agents"' in build_script
    assert "--collect-all fitz" in build_script


def test_onefile_local_artifacts_are_ignored() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "packaging/windows-onefile/output/" in gitignore
    assert "packaging/windows-onefile/work/" in gitignore
    assert "packaging/windows-onefile/.venv/" in gitignore
