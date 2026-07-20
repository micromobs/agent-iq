[CmdletBinding()]
param(
    [string]$ProjectEndpoint = $env:AGENT_IQ_FOUNDRY_PROJECT_ENDPOINT,
    [string]$ReportDirectory = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "dev_env.ps1")
. (Join-Path $PSScriptRoot "foundry_login.ps1") -Quiet

if (-not $ReportDirectory) {
    $ReportDirectory = Join-Path $root "reports\evals\foundry-a2a"
}

Push-Location $root
try {
    & uv run python .\scripts\run_foundry_a2a_conformance.py `
        --project-endpoint $ProjectEndpoint `
        --report-dir $ReportDirectory
    if ($LASTEXITCODE -ne 0) {
        throw "Foundry native-A2A conformance failed."
    }
}
finally {
    Pop-Location
}
