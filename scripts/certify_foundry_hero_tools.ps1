[CmdletBinding()]
param(
    [string]$ProjectEndpoint = $env:AGENT_IQ_FOUNDRY_PROJECT_ENDPOINT,
    [string]$AccountName = $env:AGENT_IQ_AZURE_AI_ACCOUNT,
    [string]$ProjectName = $env:AGENT_IQ_AZURE_AI_PROJECT,
    [string]$ApplicationInsightsResourceGroup = $env:AGENT_IQ_RESOURCE_GROUP,
    [string]$ApplicationInsightsName = $env:AGENT_IQ_APPLICATION_INSIGHTS,
    [string]$ArtifactRoot = (
        Join-Path $env:LOCALAPPDATA "AgentIQ\foundry\consumer"
    ),
    [hashtable]$AgentVersions = @{},
    [string]$ReportDirectory = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

if (
    [string]::IsNullOrWhiteSpace($AccountName) -or
    $AccountName -cnotmatch "^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"
) {
    throw "AccountName is not a canonical Foundry management-plane coordinate."
}
if (
    [string]::IsNullOrWhiteSpace($ProjectName) -or
    $ProjectName -cnotmatch "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
) {
    throw "ProjectName is not a canonical Foundry management-plane coordinate."
}
$boundProjectEndpoint = (
    "https://$AccountName.services.ai.azure.com/api/projects/$ProjectName"
)
if ($ProjectEndpoint -cne $boundProjectEndpoint) {
    throw "ProjectEndpoint does not match AccountName and ProjectName."
}

if (-not $ReportDirectory) {
    $ReportDirectory = Join-Path $root "reports\evals\hero-tools-live"
}

$heroIds = @(
    "consumer-brand-to-brand-compare",
    "consumer-demand-forecasting",
    "consumer-formulation-rd",
    "consumer-manufacturing-operations",
    "consumer-regulatory-affairs"
)
if (
    $AgentVersions.Count -ne $heroIds.Count -or
    @($heroIds | Where-Object { -not $AgentVersions.ContainsKey($_) }).Count -ne 0
) {
    throw "AgentVersions must provide one exact immutable version for every hero agent."
}
$agentVersionArguments = @()
foreach ($heroId in $heroIds) {
    $version = [string]$AgentVersions[$heroId]
    if (
        [string]::IsNullOrWhiteSpace($version) -or
        $version -cne $version.Trim() -or
        $version.StartsWith("@") -or
        $version -match "[/\\?#%\x00-\x20\x7f]"
    ) {
        throw "AgentVersions contains an invalid immutable version for $heroId."
    }
    $agentVersionArguments += @("--agent-version", "$heroId=$version")
}

. (Join-Path $PSScriptRoot "dev_env.ps1")
. (Join-Path $PSScriptRoot "foundry_login.ps1") -Quiet
$env:PYTHONIOENCODING = "utf-8"

& (Join-Path $PSScriptRoot "generate_consumer_deployments.ps1") `
    -Only $heroIds `
    -Out $ArtifactRoot
if ($LASTEXITCODE -ne 0) {
    throw "Hero deployment artifact generation failed."
}

$applicationId = & az monitor app-insights component show `
    --resource-group $ApplicationInsightsResourceGroup `
    --app $ApplicationInsightsName `
    --query appId -o tsv --only-show-errors
if ($LASTEXITCODE -ne 0 -or -not $applicationId) {
    throw "Application Insights component is unavailable."
}

Push-Location $root
try {
    $arguments = @(
        "run",
        "python",
        ".\scripts\run_live_hero_tool_canary.py",
        "--project-endpoint",
        $ProjectEndpoint,
        "--account-name",
        $AccountName,
        "--project-name",
        $ProjectName,
        "--app-insights-application-id",
        $applicationId,
        "--artifact-root",
        $ArtifactRoot,
        "--report-dir",
        $ReportDirectory
    ) + $agentVersionArguments
    & uv @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Live Foundry hero tool certification failed."
    }
}
finally {
    Pop-Location
}

Write-Host "Foundry hero tool certification complete." -ForegroundColor Green
