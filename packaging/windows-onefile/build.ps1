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

Push-Location $RepoRoot
try {
    if ($InstallBuildTool) {
        if (-not (Test-Path -LiteralPath $BuildPython)) {
            python -m venv $BuildVenv
        }
        & $BuildPython -m pip install --upgrade pip
        & $BuildPython -m pip install -e . "pyinstaller>=6.11,<7"
    }

    if (-not (Test-Path -LiteralPath $BuildPython)) {
        throw "没有隔离打包环境。请先运行：.\packaging\windows-onefile\build.ps1 -InstallBuildTool"
    }

    & $BuildPython -c "import PyInstaller" 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "隔离环境中没有 PyInstaller。请运行：.\packaging\windows-onefile\build.ps1 -InstallBuildTool"
    }

    & $BuildPython -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --name SeeFurther `
        --distpath $OutputDir `
        --workpath $WorkDir `
        --specpath $WorkDir `
        --add-data "$StaticDir;gateway\static" `
        --add-data "$BuiltinSkillsDir;skills\builtin" `
        --add-data "$AgentProfiles;agents" `
        --collect-all fitz `
        --collect-all pymupdf `
        --collect-submodules handlers `
        --collect-submodules skills `
        $Launcher

    $ExePath = Join-Path $OutputDir "SeeFurther.exe"
    if (-not (Test-Path -LiteralPath $ExePath)) {
        throw "打包命令结束，但没有生成 $ExePath"
    }
    Write-Host "已生成：$ExePath"
}
finally {
    Pop-Location
}
