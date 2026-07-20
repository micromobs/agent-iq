param(
    [ValidateSet("catalog", "scenario", "intent", "action", "rehearse", "preview")]
    [string]$Mode = "preview",
    [string]$Scenario,
    [string]$Intent,
    [string]$ActionId,
    [string[]]$Field = @(),
    [switch]$Confirm,
    [switch]$Json,
    [switch]$ExportOnly,
    [switch]$NoBrowser,
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "dev_env.ps1")
$env:PYTHONPATH = $root

$arguments = @("run", "python", ".\scripts\employee_showcase.py", $Mode, "--port", "$Port")
if ($Scenario) { $arguments += @("--scenario", $Scenario) }
if ($Intent) { $arguments += @("--intent", $Intent) }
if ($ActionId) { $arguments += @("--action-id", $ActionId) }
foreach ($item in $Field) { $arguments += @("--field", $item) }
if ($Confirm) { $arguments += "--confirm" }
if ($Json) { $arguments += "--json" }
if ($ExportOnly) { $arguments += "--export-only" }
if ($NoBrowser) { $arguments += "--no-browser" }

Push-Location $root
try {
    & uv @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Employee Operations showcase command failed."
    }
}
finally {
    Pop-Location
}
