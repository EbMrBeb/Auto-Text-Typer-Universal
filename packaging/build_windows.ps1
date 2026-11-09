<#
.SYNOPSIS
    Builds the Windows portable .zip and Inno Setup installer for Auto Text Typer.

.DESCRIPTION
    Runs PyInstaller against packaging/pyinstaller/auto_text_typer.spec, zips
    the resulting onedir build into a portable .zip, then compiles
    packaging/windows/installer.iss (via Inno Setup's `iscc`) into a
    per-user installer .exe. Both outputs land in dist/.

    Only the entry script is parameterised here (as ATT_ENTRY, read the same
    way the spec itself reads it, and by build_macos.sh / build_linux.sh) --
    unlike macOS, PyInstaller has no universal-binary concept on Windows, so
    there is no ATT_TARGET_ARCH equivalent to thread through.

.EXAMPLE
    packaging/build_windows.ps1
#>
[CmdletBinding()]
param(
    [string]$AttEntry = $(if ($env:ATT_ENTRY) { $env:ATT_ENTRY } else { "packaging/launch_gui.py" })
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

if (-not (Test-Path $AttEntry)) {
    throw "ATT_ENTRY script '$AttEntry' not found (relative to $RepoRoot)"
}
# Re-export it (a -AttEntry passed as a CLI arg rather than already in the
# environment) so the PyInstaller spec -- which only ever reads the env var,
# never a CLI arg -- sees the same value this script resolved.
$env:ATT_ENTRY = $AttEntry

function Resolve-Tool {
    param([string]$VenvRelative, [string]$FallbackName)
    $venvPath = Join-Path $RepoRoot $VenvRelative
    if (Test-Path $venvPath) { return $venvPath }
    return $FallbackName
}

$Python = Resolve-Tool ".venv\Scripts\python.exe" "python"
$PyInstaller = Resolve-Tool ".venv\Scripts\pyinstaller.exe" "pyinstaller"

$Version = (& $Python -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])").Trim()
if ($LASTEXITCODE -ne 0 -or -not $Version) {
    throw "failed to read project version from pyproject.toml"
}

$AppName = "AutoTextTyper"
$DistDir = Join-Path $RepoRoot "dist"
$AppDir = Join-Path $DistDir $AppName

Write-Host "==> Building $AppName $Version from $AttEntry"
Remove-Item -Recurse -Force (Join-Path $RepoRoot "build") -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $AppDir -ErrorAction SilentlyContinue

& $PyInstaller --noconfirm --clean "packaging/pyinstaller/auto_text_typer.spec"
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed with exit code $LASTEXITCODE"
}

$ExePath = Join-Path $AppDir "$AppName.exe"
if (-not (Test-Path $ExePath)) {
    throw "expected executable not found at $ExePath"
}

$ZipPath = Join-Path $DistDir "AutoTextTyper-$Version-Windows-x64-portable.zip"
Write-Host "==> Building $ZipPath"
if (Test-Path $ZipPath) { Remove-Item $ZipPath }
Compress-Archive -Path $AppDir -DestinationPath $ZipPath -CompressionLevel Optimal

Write-Host "==> Locating Inno Setup (iscc)"
$IsccPath = $null
$onPath = Get-Command "iscc" -ErrorAction SilentlyContinue
if ($onPath) {
    $IsccPath = $onPath.Source
} else {
    foreach ($candidate in @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
    )) {
        if ($candidate -and (Test-Path $candidate)) {
            $IsccPath = $candidate
            break
        }
    }
}
if (-not $IsccPath) {
    throw "iscc (Inno Setup's command-line compiler) was not found on PATH or in the usual install locations. Install Inno Setup first, e.g.: choco install innosetup -y"
}

$SetupExeName = "AutoTextTyper-$Version-Windows-x64-Setup"
$SetupExePath = Join-Path $DistDir "$SetupExeName.exe"
Write-Host "==> Building $SetupExePath"
& $IsccPath "/DAppVersion=$Version" "packaging/windows/installer.iss"
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup compilation failed with exit code $LASTEXITCODE"
}
if (-not (Test-Path $SetupExePath)) {
    throw "Inno Setup reported success but $SetupExePath was not found -- check installer.iss's OutputBaseFilename/OutputDir defaults"
}

Write-Host "==> Done."
Write-Host "    Onedir: $AppDir"
Write-Host "    Zip:    $ZipPath"
Write-Host "    Setup:  $SetupExePath"
