[CmdletBinding()]
param(
    [string]$ProjectEndpoint = $env:AGENT_IQ_FOUNDRY_PROJECT_ENDPOINT,
    [string]$SubscriptionId = $env:AZURE_SUBSCRIPTION_ID,
    [string]$ResourceGroup = $env:AGENT_IQ_AZURE_RESOURCE_GROUP,
    [string]$AccountName = $env:AGENT_IQ_AZURE_AI_ACCOUNT,
    [string]$ProjectName = $env:AGENT_IQ_AZURE_AI_PROJECT,
    [string]$Location,
    [string]$CanaryAgent = "consumer-formulation-rd-host-canary",
    [string]$Certificate,
    [string]$ReportRoot
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "dev_env.ps1")
. (Join-Path $PSScriptRoot "foundry_login.ps1") -Quiet

if (-not $ReportRoot) {
    $ReportRoot = Join-Path $root "reports\deployments\foundry\capabilities"
}
if (-not $Certificate) {
    $Certificate = Join-Path $root (
        "reports\deployments\foundry\descriptor-host-canary\latest.json"
    )
}
if (-not $SubscriptionId) {
    $SubscriptionId = & az account show --query id -o tsv --only-show-errors
}
if (-not $ProjectEndpoint -and (Test-Path $Certificate)) {
    $certificateJson = Get-Content -Raw $Certificate | ConvertFrom-Json
    $ProjectEndpoint = $certificateJson.plan.projectEndpoint
}
if (-not $ResourceGroup -and $AccountName) {
    $ResourceGroup = & az resource list `
        --name $AccountName `
        --resource-type Microsoft.CognitiveServices/accounts `
        --query "[0].resourceGroup" `
        -o tsv `
        --only-show-errors
}
if (-not $Location -and $ResourceGroup -and $AccountName) {
    $Location = & az cognitiveservices account show `
        --resource-group $ResourceGroup `
        --name $AccountName `
        --query location `
        -o tsv `
        --only-show-errors
}

foreach ($required in @{
    ProjectEndpoint = $ProjectEndpoint
    SubscriptionId = $SubscriptionId
    ResourceGroup = $ResourceGroup
    AccountName = $AccountName
    ProjectName = $ProjectName
    Location = $Location
    CanaryAgent = $CanaryAgent
}.GetEnumerator()) {
    if (-not $required.Value) {
        throw "$($required.Key) is required and could not be inferred."
    }
}

$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$runRoot = Join-Path $ReportRoot $stamp
$runPath = Join-Path $runRoot "capabilities.json"
$latestPath = Join-Path $ReportRoot "latest.json"
New-Item -ItemType Directory -Force -Path $runRoot | Out-Null
$env:PYTHONIOENCODING = "utf-8"

$pythonArguments = @(
    "-m", "agent_iq.deployments.foundry_capabilities",
    "--project-endpoint", $ProjectEndpoint,
    "--subscription-id", $SubscriptionId,
    "--resource-group", $ResourceGroup,
    "--account-name", $AccountName,
    "--project-name", $ProjectName,
    "--location", $Location,
    "--canary-agent", $CanaryAgent,
    "--certificate", $Certificate,
    "--out", $runPath
)

Push-Location $root
try {
    & uv run --all-groups python @pythonArguments
    $probeExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
if (Test-Path $runPath) {
    Copy-Item -Force $runPath $latestPath
}
if ($probeExitCode -ne 0) {
    throw "Foundry capability ledger failed. Evidence: $runPath"
}

$report = Get-Content -Raw $runPath | ConvertFrom-Json
Write-Host (
    "Foundry capability ledger: {0} ({1}/{2} required available)" -f `
        $report.status.ToUpperInvariant(),
        $report.summary.requiredAvailableCount,
        $report.summary.requiredCount
)
Write-Host "Evidence: $runPath"
