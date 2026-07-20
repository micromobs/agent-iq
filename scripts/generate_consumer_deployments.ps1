param(
    [string[]]$Only = @(),
    [string]$Out = (Join-Path $env:LOCALAPPDATA "AgentIQ\foundry\consumer"),
    [string]$Model = "gpt-5.4-mini",
    [string]$ModelVersion = "2026-03-17",
    [string]$ModelSku = "GlobalStandard",
    [int]$ModelCapacity = 10,
    [switch]$DryRun,
    [switch]$SkipValidation
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "dev_env.ps1")

$selection = @("--all")
if ($Only.Count -gt 0) {
    $selection = @()
    foreach ($agentId in $Only) {
        $selection += @("--only", $agentId)
    }
}

$generateArgs = @(
    "run",
    "python",
    "deployments/foundry/consumer/generate.py"
) + $selection + @(
    "--out",
    $Out,
    "--model",
    $Model,
    "--model-version",
    $ModelVersion,
    "--model-sku",
    $ModelSku,
    "--model-capacity",
    $ModelCapacity
)
if ($DryRun) {
    $generateArgs += "--dry-run"
}
Push-Location $root
try {
    & uv @generateArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Foundry deployment generation failed with exit code $LASTEXITCODE."
    }
    if (-not $DryRun -and -not $SkipValidation) {
        $validateArgs = @(
            "run",
            "python",
            "deployments/foundry/consumer/validate.py"
        ) + $selection + @("--out", $Out)
        & uv @validateArgs
        if ($LASTEXITCODE -ne 0) {
            throw "Foundry deployment validation failed with exit code $LASTEXITCODE."
        }
    }
}
finally {
    Pop-Location
}
