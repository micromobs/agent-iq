[CmdletBinding()]
param(
    [switch]$ProbeOnly,
    [switch]$Rollback,
    [string]$ProjectEndpoint = "",
    [string]$ResourceGroup = $env:AGENT_IQ_RESOURCE_GROUP,
    [string]$AccountName = $env:AGENT_IQ_AZURE_AI_ACCOUNT,
    [string]$ProjectName = $env:AGENT_IQ_AZURE_AI_PROJECT,
    [string]$SearchResourceGroup = $env:AGENT_IQ_SEARCH_RESOURCE_GROUP,
    [string]$SearchService = $env:AGENT_IQ_SEARCH_SERVICE,
    [string]$OrchestratorBaseUrl = $env:AGENT_IQ_ORCHESTRATOR_BASE_URL,
    [string]$OrchestratorAppName = "agent-iq-orchestrator-canary"
)

$ErrorActionPreference = "Stop"
$deployer = Join-Path $PSScriptRoot "deploy_consumer_fleet.ps1"
$invokeArguments = @{
    Only = @("consumer-formulation-rd")
    Runs = 2
    ProjectEndpoint = $ProjectEndpoint
    ResourceGroup = $ResourceGroup
    AccountName = $AccountName
    ProjectName = $ProjectName
    SearchResourceGroup = $SearchResourceGroup
    SearchService = $SearchService
    OrchestratorBaseUrl = $OrchestratorBaseUrl
    OrchestratorAppName = $OrchestratorAppName
}
if ($ProbeOnly) {
    $invokeArguments.ProbeOnly = $true
}
if ($Rollback) {
    $invokeArguments.Rollback = $true
}

& $deployer @invokeArguments
if ($LASTEXITCODE -ne 0) {
    throw "Consumer native-A2A canary operation failed."
}
