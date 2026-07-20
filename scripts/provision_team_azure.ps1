[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[a-z0-9][a-z0-9-]{1,14}[a-z0-9]$")]
    [string]$EnvironmentName,
    [string]$SubscriptionId = $env:AZURE_SUBSCRIPTION_ID,
    [string]$Location = "swedencentral",
    [string]$ResourceGroup = "",
    [string]$AiAccount = "",
    [string]$FoundryProject = "",
    [string]$SearchService = "",
    [string]$AcrName = "",
    [string]$ContainerAppsEnvironment = "",
    [string]$OrchestratorAppName = "agent-iq-orchestrator-canary",
    [string]$ModelDeployment = "gpt-5.4-mini",
    [string]$ModelName = "gpt-5.4-mini",
    [string]$ModelVersion = "2026-03-17",
    [ValidateRange(1, 1000)]
    [int]$ModelCapacity = 10,
    [string]$EmbeddingDeployment = "text-embedding-3-large-gs",
    [string]$EmbeddingModel = "text-embedding-3-large",
    [string]$EmbeddingVersion = "1",
    [ValidateRange(1, 1000)]
    [int]$EmbeddingCapacity = 10,
    [ValidateSet("Basic", "Standard", "Standard2", "Standard3")]
    [string]$SearchSku = "Basic",
    [string]$ArtifactCompanionApplicationId = "",
    [string]$OutputDirectory = "",
    [switch]$SkipProviderRegistration,
    [switch]$Plan
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$requiredProviders = @(
    "Microsoft.App",
    "Microsoft.CognitiveServices",
    "Microsoft.ContainerRegistry",
    "Microsoft.Insights",
    "Microsoft.OperationalInsights",
    "Microsoft.Search"
)

function Get-StableSuffix {
    param(
        [Parameter(Mandatory = $true)][string]$Subscription,
        [Parameter(Mandatory = $true)][string]$Environment
    )

    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes(
            "agent-iq:$($Subscription.ToLowerInvariant()):$Environment"
        )
        return [Convert]::ToHexString($sha.ComputeHash($bytes)).
            ToLowerInvariant().Substring(0, 8)
    }
    finally {
        $sha.Dispose()
    }
}

function Get-StableGuid {
    param(
        [Parameter(Mandatory = $true)][string]$Seed
    )

    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Seed))[0..15]
        $bytes[6] = ($bytes[6] -band 0x0f) -bor 0x50
        $bytes[8] = ($bytes[8] -band 0x3f) -bor 0x80
        return ([guid]::new([byte[]]$bytes)).ToString("D")
    }
    finally {
        $sha.Dispose()
    }
}

function Assert-Command {
    param([Parameter(Mandatory = $true)][string]$Name)

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' is not installed or not on PATH."
    }
}

function Invoke-Az {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Failure
    )

    $result = & az @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw $Failure
    }
    return $result
}

function Ensure-Role {
    param(
        [Parameter(Mandatory = $true)][string]$ObjectId,
        [Parameter(Mandatory = $true)][string]$PrincipalType,
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)][string]$Scope
    )

    $existing = & az role assignment list `
        --assignee-object-id $ObjectId `
        --scope $Scope `
        --role $Role `
        --query "[0].id" `
        --output tsv `
        --only-show-errors
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect role '$Role' at '$Scope'."
    }
    if ($existing) {
        return
    }

    for ($attempt = 1; $attempt -le 6; $attempt++) {
        & az role assignment create `
            --assignee-object-id $ObjectId `
            --assignee-principal-type $PrincipalType `
            --role $Role `
            --scope $Scope `
            --only-show-errors *> $null
        if ($LASTEXITCODE -eq 0) {
            return
        }
        if ($attempt -lt 6) {
            Start-Sleep -Seconds 10
        }
    }
    throw "Unable to assign role '$Role' at '$Scope'."
}

function ConvertTo-SingleQuotedLiteral {
    param([Parameter(Mandatory = $true)][string]$Value)
    return "'" + $Value.Replace("'", "''") + "'"
}

if ([string]::IsNullOrWhiteSpace($SubscriptionId)) {
    if ($Plan) {
        throw "SubscriptionId is required with -Plan."
    }
    $SubscriptionId = [string](
        Invoke-Az -Arguments @(
            "account", "show", "--query", "id", "--output", "tsv", "--only-show-errors"
        ) -Failure "Azure login is unavailable. Run 'az login' first."
    ).Trim()
}
$subscriptionGuid = [guid]::Empty
if (
    -not [guid]::TryParseExact($SubscriptionId, "D", [ref]$subscriptionGuid) -or
    $subscriptionGuid -eq [guid]::Empty
) {
    throw "SubscriptionId must be a canonical nonzero UUID."
}
$SubscriptionId = $subscriptionGuid.ToString("D")

$suffix = Get-StableSuffix -Subscription $SubscriptionId -Environment $EnvironmentName
$compactEnvironment = $EnvironmentName.Replace("-", "")
$prefix = "aiq-$EnvironmentName-$suffix"
if ([string]::IsNullOrWhiteSpace($ResourceGroup)) {
    $ResourceGroup = "rg-$prefix"
}
if ([string]::IsNullOrWhiteSpace($AiAccount)) {
    $AiAccount = "$prefix-ai"
}
if ([string]::IsNullOrWhiteSpace($FoundryProject)) {
    $FoundryProject = "agent-iq-$EnvironmentName"
}
if ([string]::IsNullOrWhiteSpace($SearchService)) {
    $SearchService = "$prefix-search"
}
if ([string]::IsNullOrWhiteSpace($AcrName)) {
    $AcrName = "aiq$compactEnvironment$suffix"
}
if ([string]::IsNullOrWhiteSpace($ContainerAppsEnvironment)) {
    $ContainerAppsEnvironment = "$prefix-env"
}
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $env:LOCALAPPDATA "AgentIQ\environments\$EnvironmentName"
}
if ($OrchestratorAppName -cnotmatch "^agent-iq-orchestrator-canary(?:-[a-z0-9]+)?$") {
    throw "OrchestratorAppName must use the Agent IQ canary naming convention."
}

$foundryProjectEndpoint = (
    "https://$AiAccount.services.ai.azure.com/api/projects/$FoundryProject"
)
$azureOpenAiEndpoint = "https://$AiAccount.cognitiveservices.azure.com/"
$searchEndpoint = "https://$SearchService.search.windows.net"
$coworkAppId = Get-StableGuid -Seed "agent-iq-cowork:${SubscriptionId}:$EnvironmentName"
$artifactCompanionOwnershipMarker = "AgentIQ.Managed.$(
    Get-StableGuid -Seed "agent-iq-artifact:${SubscriptionId}:$EnvironmentName"
)"
$artifactCompanionDisplayName = "Agent IQ $EnvironmentName $suffix Artifact Companion"
$plannedArtifactCompanionApplicationId = if ($ArtifactCompanionApplicationId) {
    $ArtifactCompanionApplicationId
}
else {
    "<created-during-provisioning>"
}
$planRecord = [ordered]@{
    schemaVersion = "1.0"
    environmentName = $EnvironmentName
    subscriptionId = $SubscriptionId
    location = $Location
    resourceGroup = $ResourceGroup
    aiAccount = $AiAccount
    foundryProject = $FoundryProject
    foundryProjectEndpoint = $foundryProjectEndpoint
    azureOpenAiEndpoint = $azureOpenAiEndpoint
    modelDeployment = $ModelDeployment
    embeddingDeployment = $EmbeddingDeployment
    searchService = $SearchService
    searchSku = $SearchSku
    searchEndpoint = $searchEndpoint
    acrName = $AcrName
    containerAppsEnvironment = $ContainerAppsEnvironment
    orchestratorAppName = $OrchestratorAppName
    artifactCompanionApplicationId = $plannedArtifactCompanionApplicationId
    artifactCompanionDisplayName = $artifactCompanionDisplayName
    coworkAppId = $coworkAppId
    outputDirectory = $OutputDirectory
}
if ($Plan) {
    $planRecord | ConvertTo-Json -Depth 5
    return
}

Assert-Command -Name "az"
Assert-Command -Name "azd"

. (Join-Path $PSScriptRoot "foundry_login.ps1") `
    -SubscriptionId $SubscriptionId `
    -Quiet

$account = Invoke-Az -Arguments @(
    "account", "show", "--query",
    "{id:id,name:name,state:state,tenantId:tenantId,user:user}",
    "--output", "json", "--only-show-errors"
) -Failure "Unable to inspect the selected Azure subscription." | ConvertFrom-Json
if ($account.id -cne $SubscriptionId) {
    throw "Azure CLI selected a different subscription than requested."
}
if ($account.state -cne "Enabled") {
    throw "Azure subscription '$($account.name)' is $($account.state); provisioning requires Enabled."
}
$tenantId = [string]$account.tenantId

if (-not $SkipProviderRegistration) {
    foreach ($provider in $requiredProviders) {
        $state = & az provider show `
            --namespace $provider `
            --query registrationState `
            --output tsv `
            --only-show-errors 2>$null
        if ($state -cne "Registered") {
            Write-Host "Registering $provider..." -ForegroundColor Cyan
            Invoke-Az -Arguments @(
                "provider", "register", "--namespace", $provider, "--wait", "--only-show-errors"
            ) -Failure "Unable to register Azure provider '$provider'." | Out-Null
        }
    }
}

$groupId = & az group show `
    --name $ResourceGroup `
    --query id `
    --output tsv `
    --only-show-errors 2>$null
if (-not $groupId) {
    Write-Host "Creating resource group $ResourceGroup..." -ForegroundColor Cyan
    $groupId = (
        Invoke-Az -Arguments @(
            "group", "create",
            "--name", $ResourceGroup,
            "--location", $Location,
            "--query", "id",
            "--output", "tsv",
            "--only-show-errors"
        ) -Failure "Unable to create resource group '$ResourceGroup'."
    ).Trim()
}

$accountId = & az cognitiveservices account show `
    --name $AiAccount `
    --resource-group $ResourceGroup `
    --query id `
    --output tsv `
    --only-show-errors 2>$null
if (-not $accountId) {
    Write-Host "Creating Foundry account $AiAccount..." -ForegroundColor Cyan
    Invoke-Az -Arguments @(
        "cognitiveservices", "account", "create",
        "--name", $AiAccount,
        "--resource-group", $ResourceGroup,
        "--kind", "AIServices",
        "--sku", "S0",
        "--location", $Location,
        "--custom-domain", $AiAccount,
        "--allow-project-management", "true",
        "--assign-identity",
        "--yes",
        "--only-show-errors"
    ) -Failure "Unable to create Foundry account '$AiAccount'." | Out-Null
    $accountId = (
        Invoke-Az -Arguments @(
            "cognitiveservices", "account", "show",
            "--name", $AiAccount,
            "--resource-group", $ResourceGroup,
            "--query", "id",
            "--output", "tsv",
            "--only-show-errors"
        ) -Failure "Unable to resolve Foundry account '$AiAccount'."
    ).Trim()
}

$project = & az cognitiveservices account project show `
    --name $AiAccount `
    --resource-group $ResourceGroup `
    --project-name $FoundryProject `
    --output json `
    --only-show-errors 2>$null | ConvertFrom-Json
if ($null -eq $project) {
    Write-Host "Creating Foundry project $FoundryProject..." -ForegroundColor Cyan
    $project = Invoke-Az -Arguments @(
        "cognitiveservices", "account", "project", "create",
        "--name", $AiAccount,
        "--resource-group", $ResourceGroup,
        "--project-name", $FoundryProject,
        "--location", $Location,
        "--assign-identity",
        "--display-name", "Agent IQ $EnvironmentName",
        "--description", "Agent IQ team environment",
        "--output", "json",
        "--only-show-errors"
    ) -Failure "Unable to create Foundry project '$FoundryProject'." | ConvertFrom-Json
}
if (
    $project.properties.provisioningState -cne "Succeeded" -or
    [string]::IsNullOrWhiteSpace([string]$project.identity.principalId)
) {
    throw "Foundry project '$FoundryProject' is not ready with a system identity."
}

$deployments = @(
    & az cognitiveservices account deployment list `
        --name $AiAccount `
        --resource-group $ResourceGroup `
        --query "[].name" `
        --output tsv `
        --only-show-errors
)
if ($ModelDeployment -notin $deployments) {
    Write-Host "Deploying model $ModelDeployment..." -ForegroundColor Cyan
    Invoke-Az -Arguments @(
        "cognitiveservices", "account", "deployment", "create",
        "--name", $AiAccount,
        "--resource-group", $ResourceGroup,
        "--deployment-name", $ModelDeployment,
        "--model-name", $ModelName,
        "--model-version", $ModelVersion,
        "--model-format", "OpenAI",
        "--sku-name", "GlobalStandard",
        "--sku-capacity", [string]$ModelCapacity,
        "--only-show-errors"
    ) -Failure "Unable to deploy model '$ModelDeployment'. Check regional availability and quota." |
        Out-Null
}
if ($EmbeddingDeployment -notin $deployments) {
    Write-Host "Deploying embedding model $EmbeddingDeployment..." -ForegroundColor Cyan
    Invoke-Az -Arguments @(
        "cognitiveservices", "account", "deployment", "create",
        "--name", $AiAccount,
        "--resource-group", $ResourceGroup,
        "--deployment-name", $EmbeddingDeployment,
        "--model-name", $EmbeddingModel,
        "--model-version", $EmbeddingVersion,
        "--model-format", "OpenAI",
        "--sku-name", "GlobalStandard",
        "--sku-capacity", [string]$EmbeddingCapacity,
        "--only-show-errors"
    ) -Failure (
        "Unable to deploy embedding model '$EmbeddingDeployment'. " +
        "Check regional availability and quota."
    ) | Out-Null
}

$search = & az search service show `
    --name $SearchService `
    --resource-group $ResourceGroup `
    --output json `
    --only-show-errors 2>$null | ConvertFrom-Json
if ($null -eq $search) {
    Write-Host "Creating Azure AI Search $SearchService..." -ForegroundColor Cyan
    $search = Invoke-Az -Arguments @(
        "search", "service", "create",
        "--name", $SearchService,
        "--resource-group", $ResourceGroup,
        "--location", $Location,
        "--sku", $SearchSku.ToLowerInvariant(),
        "--partition-count", "1",
        "--replica-count", "1",
        "--identity-type", "SystemAssigned",
        "--output", "json",
        "--only-show-errors"
    ) -Failure "Unable to create Azure AI Search '$SearchService'." | ConvertFrom-Json
}
if ([string]::IsNullOrWhiteSpace([string]$search.identity.principalId)) {
    throw "Azure AI Search '$SearchService' does not have a system identity."
}
$searchId = [string]$search.id

$acrId = & az acr show `
    --name $AcrName `
    --resource-group $ResourceGroup `
    --query id `
    --output tsv `
    --only-show-errors 2>$null
if (-not $acrId) {
    Write-Host "Creating Azure Container Registry $AcrName..." -ForegroundColor Cyan
    $acrId = (
        Invoke-Az -Arguments @(
            "acr", "create",
            "--name", $AcrName,
            "--resource-group", $ResourceGroup,
            "--location", $Location,
            "--sku", "Basic",
            "--admin-enabled", "false",
            "--public-network-enabled", "true",
            "--query", "id",
            "--output", "tsv",
            "--only-show-errors"
        ) -Failure "Unable to create Azure Container Registry '$AcrName'."
    ).Trim()
}

$workspaceName = "agent-iq-orchestrator-law"
$workspace = & az monitor log-analytics workspace show `
    --workspace-name $workspaceName `
    --resource-group $ResourceGroup `
    --output json `
    --only-show-errors 2>$null | ConvertFrom-Json
if ($null -eq $workspace) {
    Write-Host "Creating Log Analytics workspace $workspaceName..." -ForegroundColor Cyan
    $workspace = Invoke-Az -Arguments @(
        "monitor", "log-analytics", "workspace", "create",
        "--workspace-name", $workspaceName,
        "--resource-group", $ResourceGroup,
        "--location", $Location,
        "--output", "json",
        "--only-show-errors"
    ) -Failure "Unable to create Log Analytics workspace '$workspaceName'." | ConvertFrom-Json
}

$containerEnvironment = & az containerapp env show `
    --name $ContainerAppsEnvironment `
    --resource-group $ResourceGroup `
    --output json `
    --only-show-errors 2>$null | ConvertFrom-Json
if ($null -eq $containerEnvironment) {
    $workspaceCustomerId = [string]$workspace.properties.customerId
    $workspaceKey = (
        Invoke-Az -Arguments @(
            "monitor", "log-analytics", "workspace", "get-shared-keys",
            "--workspace-name", $workspaceName,
            "--resource-group", $ResourceGroup,
            "--query", "primarySharedKey",
            "--output", "tsv",
            "--only-show-errors"
        ) -Failure "Unable to configure Container Apps logging."
    ).Trim()
    Write-Host "Creating Container Apps environment $ContainerAppsEnvironment..." -ForegroundColor Cyan
    $containerEnvironment = Invoke-Az -Arguments @(
        "containerapp", "env", "create",
        "--name", $ContainerAppsEnvironment,
        "--resource-group", $ResourceGroup,
        "--location", $Location,
        "--logs-destination", "log-analytics",
        "--logs-workspace-id", $workspaceCustomerId,
        "--logs-workspace-key", $workspaceKey,
        "--output", "json",
        "--only-show-errors"
    ) -Failure "Unable to create Container Apps environment '$ContainerAppsEnvironment'." |
        ConvertFrom-Json
}
$environmentDomain = [string]$containerEnvironment.properties.defaultDomain
if ([string]::IsNullOrWhiteSpace($environmentDomain)) {
    throw "Container Apps environment '$ContainerAppsEnvironment' has no default domain."
}
$orchestratorBaseUrl = "https://$OrchestratorAppName.$environmentDomain"

if ($account.user.type -ceq "servicePrincipal") {
    $deployerObjectId = (
        Invoke-Az -Arguments @(
            "ad", "sp", "show",
            "--id", [string]$account.user.name,
            "--query", "id",
            "--output", "tsv",
            "--only-show-errors"
        ) -Failure "Unable to resolve the deployment service principal."
    ).Trim()
    $deployerPrincipalType = "ServicePrincipal"
}
else {
    $deployerObjectId = (
        Invoke-Az -Arguments @(
            "ad", "signed-in-user", "show",
            "--query", "id",
            "--output", "tsv",
            "--only-show-errors"
        ) -Failure "Unable to resolve the signed-in Azure user."
    ).Trim()
    $deployerPrincipalType = "User"
}

Ensure-Role -ObjectId $deployerObjectId -PrincipalType $deployerPrincipalType `
    -Role "AcrPush" -Scope $acrId
Ensure-Role -ObjectId $deployerObjectId -PrincipalType $deployerPrincipalType `
    -Role "Foundry Project Manager" -Scope $accountId
Ensure-Role -ObjectId $deployerObjectId -PrincipalType $deployerPrincipalType `
    -Role "Cognitive Services User" -Scope $accountId
Ensure-Role -ObjectId $deployerObjectId -PrincipalType $deployerPrincipalType `
    -Role "Search Service Contributor" -Scope $searchId
Ensure-Role -ObjectId $deployerObjectId -PrincipalType $deployerPrincipalType `
    -Role "Search Index Data Contributor" -Scope $searchId
Ensure-Role -ObjectId ([string]$project.identity.principalId) `
    -PrincipalType "ServicePrincipal" -Role "Search Index Data Reader" -Scope $searchId
Ensure-Role -ObjectId ([string]$search.identity.principalId) `
    -PrincipalType "ServicePrincipal" -Role "Cognitive Services User" -Scope $accountId

$artifactCompanionManaged = $false
if ([string]::IsNullOrWhiteSpace($ArtifactCompanionApplicationId)) {
    $artifactCompanionManaged = $true
    $displayName = $artifactCompanionDisplayName
    $apps = @(
        Invoke-Az -Arguments @(
            "ad", "app", "list",
            "--display-name", $displayName,
            "--output", "json",
            "--only-show-errors"
        ) -Failure "Unable to inspect the artifact companion application." |
            ConvertFrom-Json |
            Where-Object { $_.displayName -ceq $displayName }
    )
    if ($apps.Count -gt 1) {
        throw "Multiple Entra applications use display name '$displayName'; pass an explicit app ID."
    }
    if ($apps.Count -eq 0) {
        Write-Host "Creating Entra artifact companion application..." -ForegroundColor Cyan
        $app = Invoke-Az -Arguments @(
            "ad", "app", "create",
            "--display-name", $displayName,
            "--sign-in-audience", "AzureADMyOrg",
            "--service-management-reference", $artifactCompanionOwnershipMarker,
            "--output", "json",
            "--only-show-errors"
        ) -Failure "Unable to create the artifact companion Entra application." |
            ConvertFrom-Json
    }
    else {
        $app = $apps[0]
        if (
            [string]$app.serviceManagementReference -cne
                $artifactCompanionOwnershipMarker
        ) {
            throw (
                "The existing Entra application is not owned by this Agent IQ environment. " +
                "Pass its app ID explicitly to reuse '$displayName' without " +
                "granting teardown ownership."
            )
        }
    }
    $ArtifactCompanionApplicationId = [string]$app.appId
}
$artifactGuid = [guid]::Empty
if (
    -not [guid]::TryParseExact(
        $ArtifactCompanionApplicationId,
        "D",
        [ref]$artifactGuid
    ) -or
    $artifactGuid -eq [guid]::Empty
) {
    throw "Artifact companion application ID must be a canonical nonzero UUID."
}
$ArtifactCompanionApplicationId = $artifactGuid.ToString("D")
$artifactServicePrincipal = & az ad sp show `
    --id $ArtifactCompanionApplicationId `
    --output json `
    --only-show-errors 2>$null | ConvertFrom-Json
if ($null -eq $artifactServicePrincipal) {
    $artifactServicePrincipal = Invoke-Az -Arguments @(
        "ad", "sp", "create",
        "--id", $ArtifactCompanionApplicationId,
        "--output", "json",
        "--only-show-errors"
    ) -Failure "Unable to create the artifact companion service principal." | ConvertFrom-Json
}
if ($artifactServicePrincipal.appRoleAssignmentRequired -eq $true) {
    throw "Artifact companion service principal must not require app-role assignment."
}

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$environmentFile = Join-Path $OutputDirectory "environment.ps1"
$environmentValues = [ordered]@{
    AZURE_SUBSCRIPTION_ID = $SubscriptionId
    AZURE_TENANT_ID = $tenantId
    AGENT_IQ_ENVIRONMENT_NAME = $EnvironmentName
    AGENT_IQ_LOCATION = $Location
    AGENT_IQ_RESOURCE_GROUP = $ResourceGroup
    AGENT_IQ_CONTAINER_APPS_ENVIRONMENT = $ContainerAppsEnvironment
    AGENT_IQ_ACR_NAME = $AcrName
    AGENT_IQ_AZURE_AI_ACCOUNT = $AiAccount
    AGENT_IQ_AZURE_AI_PROJECT = $FoundryProject
    AGENT_IQ_FOUNDRY_PROJECT_ENDPOINT = $foundryProjectEndpoint
    AGENT_IQ_AZURE_OPENAI_ENDPOINT = $azureOpenAiEndpoint
    AGENT_IQ_AZURE_MODEL_DEPLOYMENT = $ModelDeployment
    AGENT_IQ_AZURE_EMBEDDING_DEPLOYMENT = $EmbeddingDeployment
    AGENT_IQ_SEARCH_RESOURCE_GROUP = $ResourceGroup
    AGENT_IQ_SEARCH_SERVICE = $SearchService
    AGENT_IQ_SEARCH_SKU = $SearchSku
    AGENT_IQ_ORCHESTRATOR_APP_NAME = $OrchestratorAppName
    AGENT_IQ_ORCHESTRATOR_BASE_URL = $orchestratorBaseUrl
    AGENT_IQ_ARTIFACT_COMPANION_APPLICATION_ID = $ArtifactCompanionApplicationId
    AGENT_IQ_ARTIFACT_COMPANION_TENANT_ID = $tenantId
    AGENT_IQ_COWORK_APP_ID = $coworkAppId
}
$lines = @(
    "# Generated by scripts\provision_team_azure.ps1. Contains no credentials."
)
foreach ($entry in $environmentValues.GetEnumerator()) {
    $literal = ConvertTo-SingleQuotedLiteral -Value ([string]$entry.Value)
    $lines += "`$env:$($entry.Key) = $literal"
}
Set-Content -Path $environmentFile -Value ($lines -join [Environment]::NewLine) -Encoding utf8

$deploymentRecord = [ordered]@{}
foreach ($entry in $planRecord.GetEnumerator()) {
    $deploymentRecord[$entry.Key] = $entry.Value
}
$deploymentRecord["generatedAt"] = (Get-Date).ToUniversalTime().ToString("o")
$deploymentRecord["status"] = "provisioned"
$deploymentRecord["tenantId"] = $tenantId
$deploymentRecord["artifactCompanionApplicationId"] = $ArtifactCompanionApplicationId
$deploymentRecord["artifactCompanionManaged"] = $artifactCompanionManaged
$deploymentRecord["artifactCompanionOwnershipMarker"] = if ($artifactCompanionManaged) {
    $artifactCompanionOwnershipMarker
}
else {
    $null
}
$deploymentRecord["orchestratorBaseUrl"] = $orchestratorBaseUrl
$deploymentRecord["coworkAppId"] = $coworkAppId
$recordPath = Join-Path $OutputDirectory "deployment.json"
$deploymentRecord | ConvertTo-Json -Depth 5 |
    Set-Content -Path $recordPath -Encoding utf8

Write-Host "`nAgent IQ Azure foundation is ready." -ForegroundColor Green
Write-Host "Environment: $environmentFile"
Write-Host "Continue with: pwsh .\scripts\agent-iq.ps1 deploy -Environment $EnvironmentName"

[pscustomobject]@{
    environmentFile = $environmentFile
    deploymentRecord = $recordPath
    orchestratorBaseUrl = $orchestratorBaseUrl
    coworkAppId = $coworkAppId
}
