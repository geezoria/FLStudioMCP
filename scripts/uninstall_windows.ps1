<#
.SYNOPSIS
    Remove the fLMCP FL Studio scripts and MCP client registrations.
#>
$repo = Split-Path -Parent $PSScriptRoot
$py = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
& $py -m fl_studio_mcp uninstall
