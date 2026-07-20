[CmdletBinding()]
param(
    [string]$ProjectEndpoint = "",
    [string]$AccountName = $env:AGENT_IQ_AZURE_AI_ACCOUNT,
    [string]$ProjectName = $env:AGENT_IQ_AZURE_AI_PROJECT,
    [string]$SearchResourceGroup = $env:AGENT_IQ_SEARCH_RESOURCE_GROUP,
    [string]$SearchService = $env:AGENT_IQ_SEARCH_SERVICE,
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
if ([string]::IsNullOrWhiteSpace($ProjectEndpoint)) {
    $ProjectEndpoint = $boundProjectEndpoint
}
if ($ProjectEndpoint -cne $boundProjectEndpoint) {
    throw "ProjectEndpoint does not match AccountName and ProjectName."
}
if (
    [string]::IsNullOrWhiteSpace($SearchResourceGroup) -or
    $SearchResourceGroup.Length -gt 90 -or
    $SearchResourceGroup -cnotmatch "^[A-Za-z0-9._()\-]+$" -or
    $SearchResourceGroup.EndsWith(".")
) {
    throw "SearchResourceGroup is not a canonical Azure resource-group coordinate."
}
if (
    [string]::IsNullOrWhiteSpace($SearchService) -or
    $SearchService -cnotmatch "^[a-z0-9](?:[a-z0-9-]{0,58}[a-z0-9])?$"
) {
    throw "SearchService is not a canonical Azure AI Search coordinate."
}

. (Join-Path $PSScriptRoot "dev_env.ps1")
. (Join-Path $PSScriptRoot "foundry_login.ps1") -Quiet
$env:PYTHONIOENCODING = "utf-8"

if (-not $ReportDirectory) {
    $ReportDirectory = Join-Path $root "reports\evals\orchestration-live"
}

$searchId = & az search service show `
    --resource-group $SearchResourceGroup `
    --name $SearchService `
    --query id -o tsv --only-show-errors
if ($LASTEXITCODE -ne 0 -or -not $searchId) {
    throw "Azure AI Search service is unavailable."
}
$deployerObjectId = & az ad sp show --id $env:AZURE_CLIENT_ID --query id -o tsv
$readerRole = & az role assignment list `
    --assignee-object-id $deployerObjectId `
    --scope $searchId `
    --query "[?roleDefinitionName=='Search Index Data Reader'].id | [0]" `
    -o tsv
if (-not $readerRole) {
    throw "Orchestration certification requires pre-provisioned Search Index Data Reader access."
}

Push-Location $root
try {
    & uv run python .\scripts\run_live_orchestration_canary.py `
        --project-endpoint $ProjectEndpoint `
        --account-name $AccountName `
        --project-name $ProjectName `
        --search-service $SearchService `
        --report-dir $ReportDirectory
    if ($LASTEXITCODE -ne 0) {
        throw "Live Foundry multi-agent orchestration certification failed."
    }
}
finally {
    Pop-Location
}

Write-Host "Foundry multi-agent orchestration certification complete." -ForegroundColor Green
