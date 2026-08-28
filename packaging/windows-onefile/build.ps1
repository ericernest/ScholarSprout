param(
    [switch]$InstallBuildTool
)

$ErrorActionPreference = "Stop"
$PackageDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $PackageDir "..\..")
$OutputDir = Join-Path $PackageDir "output"
$WorkDir = Join-Path $PackageDir "work"
$BuildVenv = Join-Path $PackageDir ".venv"
$BuildPython = Join-Path $BuildVenv "Scripts\python.exe"
$StaticDir = Join-Path $RepoRoot "gateway\static"
$BuiltinSkillsDir = Join-Path $RepoRoot "skills\builtin"
$AgentProfiles = Join-Path $RepoRoot "agents\profiles.json"
$Launcher = Join-Path $PackageDir "launcher.py"
$AppIcon = Join-Path $PackageDir "seefurther.ico"

Push-Location $RepoRoot
try {
    if ($InstallBuildTool) {
        if (-not (Test-Path -LiteralPath $BuildPython)) {
            python -m venv $BuildVenv
        }
        & $BuildPython -m pip install --upgrade pip
        & $BuildPython -m pip install -e . "pyinstaller>=6.11,<7" "Pillow>=10,<13" "pystray>=0.19,<1"
    }

    if (-not (Test-Path -LiteralPath $BuildPython)) {
        throw "没有隔离打包环境。请先运行：.\packaging\windows-onefile\build.ps1 -InstallBuildTool"
    }

    & $BuildPython -c "import PyInstaller, PIL, pystray, lark_oapi" 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "隔离环境中缺少 PyInstaller、Pillow、pystray 或 lark-oapi。请运行：.\packaging\windows-onefile\build.ps1 -InstallBuildTool"
    }

    & $BuildPython -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --name SeeFurther `
        --icon $AppIcon `
        --distpath $OutputDir `
        --workpath $WorkDir `
        --specpath $WorkDir `
        --add-data "$StaticDir;gateway\static" `
        --add-data "$BuiltinSkillsDir;skills\builtin" `
        --add-data "$AgentProfiles;agents" `
        --add-data "$AppIcon;packaging\windows-onefile" `
        --collect-all fitz `
        --collect-all pymupdf `
        --collect-all lark_oapi `
        --collect-submodules handlers `
        --collect-submodules skills `
        --hidden-import pystray._win32 `
        $Launcher

    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller 打包失败，退出码：$LASTEXITCODE"
    }

    $ExePath = Join-Path $OutputDir "SeeFurther.exe"
    if (-not (Test-Path -LiteralPath $ExePath)) {
        throw "打包命令结束，但没有生成 $ExePath"
    }
    Write-Host "已生成：$ExePath"
}
finally {
    Pop-Location
}
