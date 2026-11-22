<#
.SYNOPSIS
    Install fLMCP from a git checkout (developers). End users: `uvx flmcp-studio install`.

.DESCRIPTION
    Creates .venv, installs the package (editable) with the chosen extras and
    then runs `flmcp install`, which copies the FL Studio scripts, sets up the
    loopMIDI port + FL MIDI settings and registers the MCP server with Claude
    Desktop / Claude Code / Cursor / VS Code.

.EXAMPLE
    PS> .\scripts\install_windows.ps1              # core + audio + gui extras
    PS> .\scripts\install_windows.ps1 -SkipAudio   # core only
    PS> .\scripts\install_windows.ps1 -Polyphonic  # + Spotify Basic Pitch
#>
param(
    [switch]$SkipAudio,
    [switch]$Polyphonic,
    [switch]$SkipClientConfig,
    [string]$PythonExe = "python"
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $repo ".venv"
$py = Join-Path $venv "Scripts\python.exe"

if (-not (Test-Path $py)) { & $PythonExe -m venv $venv }
& $py -m pip install --upgrade pip --quiet
$extras = if ($SkipAudio) { "" } else { "[audio,gui]" }
& $py -m pip install -e "$repo$extras" --quiet
if ($Polyphonic) {
    # Basic Pitch pins an old TensorFlow; install its runtime deps from our extra
    # and Basic Pitch itself with --no-deps (inference runs on ONNX runtime).
    & $py -m pip install -e "$repo[polyphonic]" --quiet
    & $py -m pip install "basic-pitch>=0.4" --no-deps --quiet
}

$installArgs = @("install", "--command", "python")
if ($SkipClientConfig) { $installArgs += "--no-clients" }
& $py -m fl_studio_mcp @installArgs
