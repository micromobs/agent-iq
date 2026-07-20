[CmdletBinding()]
param(
    [string]$ProjectEndpoint = $env:FOUNDRY_PROJECT_ENDPOINT,
    [string]$AccountName = $env:FOUNDRY_ACCOUNT_NAME,
    [string]$ProjectName = $env:FOUNDRY_PROJECT_NAME,
    [string]$ReportDirectory = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "dev_env.ps1")
. (Join-Path $PSScriptRoot "foundry_login.ps1") -Quiet
$env:PYTHONIOENCODING = "utf-8"

if (-not $ProjectEndpoint -or -not $AccountName -or -not $ProjectName) {
    throw "ProjectEndpoint, AccountName, and ProjectName are required."
}
if (-not $ReportDirectory) {
    $ReportDirectory = Join-Path $root "reports\evals\foundry-native"
}

Push-Location $root
try {
    & uv run python .\scripts\run_foundry_native_eval.py `
        --project-endpoint $ProjectEndpoint `
        --account-name $AccountName `
        --project-name $ProjectName `
        --output $ReportDirectory
    if ($LASTEXITCODE -ne 0) {
        throw "Native Foundry evaluation certification failed."
    }
}
finally {
    Pop-Location
}

Write-Host "Native Foundry evaluation certification complete." -ForegroundColor Green
