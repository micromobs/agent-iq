[CmdletBinding()]
param(
    [string]$LogicalAgentId = "consumer-formulation-rd",
    [string]$DeploymentAgentId = "consumer-formulation-rd-host-canary",
    [string]$ResourceGroup = $env:AGENT_IQ_AZURE_RESOURCE_GROUP,
    [string]$AccountName = $env:AGENT_IQ_AZURE_AI_ACCOUNT,
    [string]$ProjectName = $env:AGENT_IQ_AZURE_AI_PROJECT,
    [string]$ProjectEndpoint = $env:AGENT_IQ_FOUNDRY_PROJECT_ENDPOINT,
    [string]$SearchResourceGroup = $env:AGENT_IQ_AZURE_SEARCH_RESOURCE_GROUP,
    [string]$SearchService = $env:AGENT_IQ_SEARCH_SERVICE,
    [string]$AcrResourceGroup = $env:AGENT_IQ_AZURE_ACR_RESOURCE_GROUP,
    [string]$AcrName = $env:AGENT_IQ_AZURE_CONTAINER_REGISTRY,
    [string]$MutationLeaseResourceGroup = $env:AGENT_IQ_MUTATION_LEASE_RESOURCE_GROUP,
    [string]$MutationLeaseStorageAccount = $env:AGENT_IQ_MUTATION_LEASE_STORAGE_ACCOUNT,
    [string]$MutationLeaseContainer = $env:AGENT_IQ_MUTATION_LEASE_CONTAINER,
    [string]$ImageRepository = "agent-iq/foundry-langgraph-host",
    [string]$Model = "gpt-5.4-mini",
    [ValidateSet("project-managed-identity", "agentic-identity")]
    [string]$GroundingAuthType = "project-managed-identity",
    [string]$ExistingImage,
    [string]$ReportRoot,
    [string]$RollbackRecord,
    [switch]$SkipBuild,
    [switch]$ProbeOnly,
    [switch]$Rollback
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent

function ConvertTo-CanonicalFoundryProjectEndpoint {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    $hasUnsafeCharacter = $false
    foreach ($character in $Value.ToCharArray()) {
        if (
            [char]::IsWhiteSpace($character) -or
            [char]::GetUnicodeCategory($character) -eq
                [System.Globalization.UnicodeCategory]::Control
        ) {
            $hasUnsafeCharacter = $true
            break
        }
    }
    if (
        [string]::IsNullOrWhiteSpace($Value) -or
        $Value -cne $Value.Trim() -or
        $hasUnsafeCharacter -or
        $Value.Contains("\") -or
        $Value.Contains("%") -or
        $Value.Contains("@") -or
        $Value.Contains("?") -or
        $Value.Contains("#")
    ) {
        throw "Foundry project endpoint contains unsafe characters or delimiters."
    }

    $uri = $null
    if (
        -not [System.Uri]::TryCreate(
            $Value,
            [System.UriKind]::Absolute,
            [ref]$uri
        ) -or
        $null -eq $uri -or
        $uri.Scheme -ine "https" -or
        $uri.HostNameType -ne [System.UriHostNameType]::Dns -or
        -not [string]::IsNullOrEmpty($uri.UserInfo) -or
        -not [string]::IsNullOrEmpty($uri.Query) -or
        -not [string]::IsNullOrEmpty($uri.Fragment) -or
        $uri.Authority.Contains(":")
    ) {
        throw "Foundry project endpoint must be a credential-free HTTPS URL without a port."
    }

    $endpointPattern = (
        "^(?i:https://(?<account>[a-z0-9]" +
        "(?:[a-z0-9-]{0,62}[a-z0-9])?)" +
        "\.services\.ai\.azure\.com)" +
        "(?-i:/api/projects/)" +
        "(?<project>[A-Za-z0-9][A-Za-z0-9._-]{0,127})/?$"
    )
    $match = [regex]::Match(
        $Value,
        $endpointPattern,
        [System.Text.RegularExpressions.RegexOptions]::CultureInvariant
    )
    if (-not $match.Success) {
        throw (
            "Foundry project endpoint must use the canonical " +
            "'https://<account>.services.ai.azure.com/api/projects/<project>' form."
        )
    }

    $account = $match.Groups["account"].Value.ToLowerInvariant()
    $project = $match.Groups["project"].Value
    return "https://$account.services.ai.azure.com/api/projects/$project"
}

function Get-CanonicalFoundryProjectEndpoint {
    param(
        [Parameter(Mandatory = $true)]
        [string]$AccountName,
        [Parameter(Mandatory = $true)]
        [string]$ProjectName
    )

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

    return "https://$AccountName.services.ai.azure.com/api/projects/$ProjectName"
}

function Resolve-CanonicalFoundryProjectEndpoint {
    param(
        [Parameter(Mandatory = $true)]
        [string]$AccountName,
        [Parameter(Mandatory = $true)]
        [string]$ProjectName,
        [AllowEmptyString()]
        [string]$SuppliedEndpoint
    )

    $canonical = Get-CanonicalFoundryProjectEndpoint `
        -AccountName $AccountName `
        -ProjectName $ProjectName
    if ([string]::IsNullOrEmpty($SuppliedEndpoint)) {
        return $canonical
    }

    $suppliedCanonical = ConvertTo-CanonicalFoundryProjectEndpoint `
        -Value $SuppliedEndpoint
    if ($suppliedCanonical -cne $canonical) {
        throw (
            "Supplied Foundry project endpoint does not match the configured " +
            "AccountName and ProjectName."
        )
    }
    return $canonical
}

function Assert-CanonicalFoundryProjectEndpoint {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value,
        [Parameter(Mandatory = $true)]
        [string]$AccountName,
        [Parameter(Mandatory = $true)]
        [string]$ProjectName
    )

    $canonical = Get-CanonicalFoundryProjectEndpoint `
        -AccountName $AccountName `
        -ProjectName $ProjectName
    $null = ConvertTo-CanonicalFoundryProjectEndpoint -Value $Value
    if ($Value -cne $canonical) {
        throw (
            "Foundry project endpoint did not match the configured management-plane " +
            "coordinates at the request boundary."
        )
    }
    return $canonical
}

function Assert-ResourceGroupName {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if (
        $Name.Length -gt 90 -or
        $Name.EndsWith(".", [StringComparison]::Ordinal) -or
        $Name -cnotmatch '^[A-Za-z0-9._()\-]+$'
    ) {
        throw "$Label is not a valid Azure resource-group name."
    }
}

function Assert-SearchServiceName {
    param([Parameter(Mandatory = $true)][string]$Name)

    if (
        $Name.Length -lt 2 -or
        $Name.Length -gt 60 -or
        $Name -cnotmatch '^[a-z0-9][a-z0-9-]*[a-z0-9]$' -or
        $Name.Contains("--", [StringComparison]::Ordinal)
    ) {
        throw "SearchService is not a canonical Azure AI Search service name."
    }
}

function Assert-AcrName {
    param([Parameter(Mandatory = $true)][string]$Name)

    if ($Name -cnotmatch '^[a-z0-9]{5,50}$') {
        throw "AcrName is not a canonical Azure Container Registry name."
    }
}

function Assert-StorageAccountName {
    param([Parameter(Mandatory = $true)][string]$Name)

    if ($Name -cnotmatch '^[a-z0-9]{3,24}$') {
        throw "MutationLeaseStorageAccount is not a canonical Azure Storage account name."
    }
}

function Assert-BlobContainerName {
    param([Parameter(Mandatory = $true)][string]$Name)

    if (
        $Name.Length -lt 3 -or
        $Name.Length -gt 63 -or
        $Name -cnotmatch '^[a-z0-9](?:[a-z0-9]|-(?!-))*[a-z0-9]$'
    ) {
        throw "MutationLeaseContainer is not a canonical Azure Blob container name."
    }
}

foreach ($required in @{
    ResourceGroup = $ResourceGroup
    AccountName = $AccountName
    ProjectName = $ProjectName
    SearchService = $SearchService
    AcrName = $AcrName
    MutationLeaseStorageAccount = $MutationLeaseStorageAccount
    MutationLeaseContainer = $MutationLeaseContainer
}.GetEnumerator()) {
    if (-not $required.Value) {
        throw "$($required.Key) is required as a parameter or Agent IQ environment variable."
    }
}
if (-not $SearchResourceGroup) {
    $SearchResourceGroup = $ResourceGroup
}
if (-not $AcrResourceGroup) {
    $AcrResourceGroup = $ResourceGroup
}
if (-not $MutationLeaseResourceGroup) {
    $MutationLeaseResourceGroup = $ResourceGroup
}
Assert-ResourceGroupName -Name $ResourceGroup -Label "ResourceGroup"
Assert-ResourceGroupName -Name $SearchResourceGroup -Label "SearchResourceGroup"
Assert-ResourceGroupName -Name $AcrResourceGroup -Label "AcrResourceGroup"
Assert-ResourceGroupName `
    -Name $MutationLeaseResourceGroup `
    -Label "MutationLeaseResourceGroup"
Assert-SearchServiceName -Name $SearchService
Assert-AcrName -Name $AcrName
Assert-StorageAccountName -Name $MutationLeaseStorageAccount
Assert-BlobContainerName -Name $MutationLeaseContainer
if (
    $LogicalAgentId -cnotmatch '^[a-z0-9][a-z0-9-]{1,127}$' -or
    $DeploymentAgentId -cnotmatch '^[a-z0-9][a-z0-9-]{1,127}$'
) {
    throw "LogicalAgentId and DeploymentAgentId must be canonical Agent IQ identifiers."
}
if ($ImageRepository -cnotmatch '^[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*$') {
    throw "ImageRepository is not a canonical ACR repository path."
}
if ($Model -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') {
    throw "Model is not a canonical Foundry deployment name."
}
$mutationLeaseBlobName = (
    "agent-iq/foundry/$AccountName/$ProjectName/$DeploymentAgentId.lock"
)
$mutationLeaseBlobUrl = (
    "https://$MutationLeaseStorageAccount.blob.core.windows.net/" +
    "$MutationLeaseContainer/$mutationLeaseBlobName"
)
$ProjectEndpoint = Resolve-CanonicalFoundryProjectEndpoint `
    -AccountName $AccountName `
    -ProjectName $ProjectName `
    -SuppliedEndpoint $ProjectEndpoint
if ($LogicalAgentId -eq $DeploymentAgentId) {
    throw "The isolated host canary cannot replace the routed logical agent."
}
if ($Rollback -and $ProbeOnly) {
    throw "Rollback and ProbeOnly are mutually exclusive."
}
if ($Rollback -and [string]::IsNullOrWhiteSpace($RollbackRecord)) {
    throw "Rollback requires an explicit run-specific RollbackRecord."
}
if ($SkipBuild -and [string]::IsNullOrWhiteSpace($ExistingImage)) {
    throw "SkipBuild requires ExistingImage pinned with @sha256:<digest>."
}
if (
    -not [string]::IsNullOrWhiteSpace($ExistingImage) -and
    $ExistingImage -cnotmatch (
        "^$([regex]::Escape("$AcrName.azurecr.io/$ImageRepository"))" +
        "@sha256:[0-9a-f]{64}$"
    )
) {
    throw "ExistingImage must be an exact digest in the configured ACR repository."
}

if (-not $ReportRoot) {
    $ReportRoot = Join-Path $root "reports\deployments\foundry\descriptor-host-canary"
}
$latestPath = Join-Path $ReportRoot "latest.json"
$rollbackRecordPath = $null
if ($Rollback) {
    $reportRootPath = [IO.Path]::GetFullPath($ReportRoot).TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
    $rollbackRecordPath = if ([IO.Path]::IsPathRooted($RollbackRecord)) {
        [IO.Path]::GetFullPath($RollbackRecord)
    }
    else {
        [IO.Path]::GetFullPath((Join-Path $reportRootPath $RollbackRecord))
    }
    if (
        [IO.Path]::GetFileName($rollbackRecordPath) -ieq "latest.json" -or
        -not $rollbackRecordPath.StartsWith(
            "$reportRootPath$([IO.Path]::DirectorySeparatorChar)",
            [StringComparison]::OrdinalIgnoreCase
        ) -or
        -not (Test-Path -LiteralPath $rollbackRecordPath -PathType Leaf)
    ) {
        throw "RollbackRecord must be an explicit run-specific record inside ReportRoot."
    }
}
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$runRoot = Join-Path $ReportRoot $stamp
New-Item -ItemType Directory -Force -Path $runRoot | Out-Null

function Invoke-AgentIqPython {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    Push-Location $root
    try {
        & uv run --all-groups python @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "Agent IQ Python command failed: $($Arguments -join ' ')"
        }
    }
    finally {
        Pop-Location
    }
}

function Test-Role {
    param(
        [Parameter(Mandatory = $true)][string]$ObjectId,
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)][string]$Scope
    )

    $existing = & az role assignment list `
        --assignee-object-id $ObjectId `
        --scope $Scope `
        --query "[?roleDefinitionName=='$Role'].id | [0]" `
        --output tsv `
        --only-show-errors
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to inspect $Role for $ObjectId."
    }
    return -not [string]::IsNullOrWhiteSpace(($existing | Out-String).Trim())
}

function Require-Role {
    param(
        [Parameter(Mandatory = $true)][string]$ObjectId,
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)][string]$Scope
    )

    if (-not (Test-Role -ObjectId $ObjectId -Role $Role -Scope $Scope)) {
        throw (
            "Required pre-provisioned role $Role is missing for $ObjectId. " +
            "The host canary never grants shared project RBAC."
        )
    }
}

function Get-VerifiedManifestDigest {
    param(
        [Parameter(Mandatory = $true)][string]$ManifestName,
        [Parameter(Mandatory = $true)][string]$Description
    )

    $digest = & az acr manifest show-metadata `
        --registry $AcrName `
        --name $ManifestName `
        --query digest `
        --output tsv `
        --only-show-errors
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($digest)) {
        throw "The $Description could not be resolved in Azure Container Registry."
    }
    $digest = $digest.Trim().ToLowerInvariant()
    if ($digest -cnotmatch "^sha256:[0-9a-f]{64}$") {
        throw "Azure Container Registry returned an invalid immutable digest for $Description."
    }
    return $digest
}

function Resolve-VerifiedImmutableImage {
    param([Parameter(Mandatory = $true)][string]$ImageReference)

    $expectedPrefix = "$($acr.loginServer)/${ImageRepository}@"
    if (-not $ImageReference.StartsWith(
        $expectedPrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "The image reference must target $expectedPrefix<sha256:digest>."
    }
    $requestedDigest = $ImageReference.Substring($expectedPrefix.Length).ToLowerInvariant()
    if ($requestedDigest -cnotmatch "^sha256:[0-9a-f]{64}$") {
        throw "The image reference must contain an exact immutable sha256 digest."
    }
    $resolvedDigest = Get-VerifiedManifestDigest `
        -ManifestName "${ImageRepository}@$requestedDigest" `
        -Description "requested immutable image"
    if ($resolvedDigest -cne $requestedDigest) {
        throw "The requested immutable image resolved to a different digest."
    }
    return "$($acr.loginServer)/${ImageRepository}@$resolvedDigest"
}

function Assert-ExactAgentVersion {
    param(
        [AllowNull()]
        [object]$Value,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $version = [string]$Value
    if (
        [string]::IsNullOrWhiteSpace($version) -or
        $version -cne $version.Trim() -or
        $version.StartsWith("@") -or
        $version -match "[/\\?#]" -or
        (
            $version.ToCharArray() |
                Where-Object { [char]::IsControl([char]$_) } |
                Select-Object -First 1
        )
    ) {
        throw "$Label must be an exact immutable version identifier."
    }
    return $version
}

function New-HostPlan {
    param(
        [Parameter(Mandatory = $true)][string]$Image,
        [Parameter(Mandatory = $true)][string]$OutputPath,
        [Parameter(Mandatory = $true)][string]$CardOutputPath
    )

    $pythonProjectEndpoint = Assert-CanonicalFoundryProjectEndpoint `
        -Value $ProjectEndpoint `
        -AccountName $AccountName `
        -ProjectName $ProjectName
    Invoke-AgentIqPython -Arguments @(
        "-m", "agent_iq.deployments.foundry_host",
        "plan",
        "--logical-agent-id", $LogicalAgentId,
        "--deployment-agent-id", $DeploymentAgentId,
        "--project-endpoint", $pythonProjectEndpoint,
        "--account-name", $AccountName,
        "--project-name", $ProjectName,
        "--image", $Image,
        "--model-deployment", $Model,
        "--search-service", $SearchService,
        "--output", $OutputPath,
        "--card-output", $CardOutputPath
    )
}

function Test-DescriptorImage {
    param(
        [Parameter(Mandatory = $true)][string]$Image,
        [Parameter(Mandatory = $true)][string]$PlanPath
    )

    $plan = Get-Content $PlanPath -Raw | ConvertFrom-Json
    $checkEnvironment = @(
        "-e", "AIQ_HOST_LOGICAL_AGENT_ID=$($plan.logicalAgentId)",
        "-e", "AIQ_HOST_RUNTIME_FAMILY=$($plan.runtimeDescriptor.runtimeFamily)",
        "-e", "AIQ_HOST_DESCRIPTOR_SHA256=$($plan.runtimeDescriptor.descriptorSha256)",
        "-e", "AIQ_HOST_TOOLBOX_NAME=$($plan.runtimeDescriptor.toolboxName)"
    )
    & docker run --rm --platform linux/amd64 `
        @checkEnvironment `
        $Image `
        --check
    if ($LASTEXITCODE -ne 0) {
        throw "The descriptor host image failed its network-free startup check."
    }
}

function Invoke-HostCertification {
    param(
        [Parameter(Mandatory = $true)][string]$PlanPath,
        [Parameter(Mandatory = $true)][string]$CardPath,
        [Parameter(Mandatory = $true)][string]$DeploymentPath,
        [Parameter(Mandatory = $true)][string]$ToolboxReport,
        [Parameter(Mandatory = $true)][object]$GroundingConnection,
        [Parameter(Mandatory = $true)][object]$ContainerRegistry
    )

    $deploymentRecord = Get-Content $DeploymentPath -Raw | ConvertFrom-Json
    $exactVersion = Assert-ExactAgentVersion `
        -Value $deploymentRecord.version `
        -Label "Deployment record version"
    $prompt = (
        "Which enzymes improve BrightWash Cold cleaning? " +
        "Cite the source by its exact title and reference marker."
    )
    $responsesPath = Join-Path $runRoot "responses.json"
    Invoke-AgentIqPython -Arguments @(
        "-m", "agent_iq.deployments.foundry_host",
        "probe-responses",
        "--plan", $PlanPath,
        "--account-name", $AccountName,
        "--project-name", $ProjectName,
        "--agent-version", $exactVersion,
        "--prompt", $prompt,
        "--required-term", "protease",
        "--required-term", "amylase",
        "--required-term", "mannanase",
        "--required-term", "lipase",
        "--output", $responsesPath
    )

    $a2aPath = Join-Path $runRoot "a2a.json"
    $pythonProjectEndpoint = Assert-CanonicalFoundryProjectEndpoint `
        -Value $ProjectEndpoint `
        -AccountName $AccountName `
        -ProjectName $ProjectName
    Invoke-AgentIqPython -Arguments @(
        "-m", "agent_iq.deployments.foundry_canary",
        "--project-endpoint", $pythonProjectEndpoint,
        "--account-name", $AccountName,
        "--project-name", $ProjectName,
        "--agent-id", $DeploymentAgentId,
        "--agent-version", $exactVersion,
        "--logical-agent-id", $LogicalAgentId,
        "--expected-toolbox-name", $toolboxName,
        "--canonical-card", $CardPath,
        "--build-record", $DeploymentPath,
        "--output", $a2aPath,
        "--prompt", $prompt,
        "--required-term", "protease",
        "--required-term", "amylase",
        "--required-term", "mannanase",
        "--required-term", "lipase",
        "--required-source", "BrightWash Cold active system",
        "--required-model", $Model
    )

    $toolboxRecord = Get-Content $ToolboxReport -Raw | ConvertFrom-Json
    $responsesRecord = Get-Content $responsesPath -Raw | ConvertFrom-Json
    $a2aRecord = Get-Content $a2aPath -Raw | ConvertFrom-Json
    $responsesVersion = Assert-ExactAgentVersion `
        -Value $responsesRecord.agentVersion `
        -Label "Responses certification version"
    $a2aVersion = Assert-ExactAgentVersion `
        -Value $a2aRecord.agent.version `
        -Label "A2A certification version"
    $passed = (
        $deploymentRecord.status -eq "active" -and
        $toolboxRecord.status -eq "pass" -and
        $responsesRecord.status -eq "pass" -and
        $a2aRecord.status -eq "pass" -and
        $responsesVersion -ceq $exactVersion -and
        $a2aVersion -ceq $exactVersion
    )
    $aggregate = [ordered]@{
        schemaVersion = "1.0"
        status = if ($passed) { "pass" } else { "fail" }
        checkedAt = (Get-Date).ToUniversalTime().ToString("o")
        logicalAgentId = $LogicalAgentId
        deploymentAgentId = $DeploymentAgentId
        certifiedVersion = $exactVersion
        plan = $deploymentRecord.plan
        rollback = $deploymentRecord.rollback
        deployment = $deploymentRecord
        groundingConnection = $GroundingConnection
        containerRegistry = $ContainerRegistry
        toolbox = $toolboxRecord
        responses = $responsesRecord
        a2a = $a2aRecord
    }
    $aggregate | ConvertTo-Json -Depth 60 |
        Set-Content (Join-Path $runRoot "certificate.json") -Encoding utf8
    if (-not $passed) {
        throw "Descriptor-driven Foundry host canary certification failed for version $exactVersion."
    }
    $aggregate | ConvertTo-Json -Depth 60 |
        Set-Content $latestPath -Encoding utf8

    Write-Host (
        "Descriptor host canary PASS: $LogicalAgentId -> $DeploymentAgentId " +
        "version $exactVersion ($($deploymentRecord.imageDigest))"
    ) -ForegroundColor Green
    Write-Host "Evidence: $latestPath"
}

$rollbackValidationPath = Join-Path $runRoot "rollback-validation.json"
if ($Rollback) {
    Invoke-AgentIqPython -Arguments @(
        "-m", "agent_iq.deployments.foundry_host",
        "validate-rollback",
        "--record", $rollbackRecordPath,
        "--account-name", $AccountName,
        "--project-name", $ProjectName,
        "--mutation-lease-blob-url", $mutationLeaseBlobUrl,
        "--output", $rollbackValidationPath
    )
}

. (Join-Path $PSScriptRoot "foundry_login.ps1") -Quiet

$subscriptionId = $env:AZURE_SUBSCRIPTION_ID
if (-not $subscriptionId) {
    throw "AZURE_SUBSCRIPTION_ID was not established by Foundry login."
}
$resourceGroupIds = @{}
foreach ($resourceGroupName in @(
    $ResourceGroup,
    $SearchResourceGroup,
    $AcrResourceGroup,
    $MutationLeaseResourceGroup
) | Select-Object -Unique) {
    $resourceGroupId = & az group show `
        --name $resourceGroupName `
        --query id `
        --output tsv `
        --only-show-errors
    $expectedResourceGroupId = (
        "/subscriptions/$subscriptionId/resourceGroups/$resourceGroupName"
    )
    if (
        $LASTEXITCODE -ne 0 -or
        [string]::IsNullOrWhiteSpace($resourceGroupId) -or
        $resourceGroupId.Trim() -ine $expectedResourceGroupId
    ) {
        throw "Azure resource group $resourceGroupName is unavailable or misbound."
    }
    $resourceGroupIds[$resourceGroupName] = $resourceGroupId.Trim()
}
$account = & az cognitiveservices account show `
    --resource-group $ResourceGroup `
    --name $AccountName `
    --output json `
    --only-show-errors | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $account.id) {
    throw "Foundry account $AccountName is unavailable."
}
$expectedAccountId = (
    "/subscriptions/$subscriptionId/resourceGroups/$ResourceGroup" +
    "/providers/Microsoft.CognitiveServices/accounts/$AccountName"
)
if ($account.id -ine $expectedAccountId) {
    throw "Foundry account $AccountName resolved outside its configured resource group."
}
$projectResourceId = (
    "/subscriptions/$subscriptionId" +
    "/resourceGroups/$ResourceGroup" +
    "/providers/Microsoft.CognitiveServices/accounts/$AccountName" +
    "/projects/$ProjectName"
)
$project = & az rest `
    --method get `
    --url (
        "https://management.azure.com${projectResourceId}" +
        "?api-version=2025-10-01-preview"
    ) | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $project.identity.principalId) {
    throw "Foundry project managed identity is unavailable."
}
if ($project.id -ine $projectResourceId) {
    throw "Foundry project $ProjectName resolved outside its configured account."
}
$acr = & az acr show `
    --resource-group $AcrResourceGroup `
    --name $AcrName `
    --output json `
    --only-show-errors | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $acr.id -or -not $acr.loginServer) {
    throw "Azure Container Registry $AcrName is unavailable."
}
$expectedAcrId = (
    "/subscriptions/$subscriptionId/resourceGroups/$AcrResourceGroup" +
    "/providers/Microsoft.ContainerRegistry/registries/$AcrName"
)
if (
    $acr.id -ine $expectedAcrId -or
    $acr.loginServer -cne "$AcrName.azurecr.io"
) {
    throw "Azure Container Registry $AcrName resolved outside its configured coordinates."
}
$search = & az search service show `
    --resource-group $SearchResourceGroup `
    --name $SearchService `
    --output json `
    --only-show-errors | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $search.id) {
    throw "Azure AI Search service $SearchService is unavailable."
}
$expectedSearchId = (
    "/subscriptions/$subscriptionId/resourceGroups/$SearchResourceGroup" +
    "/providers/Microsoft.Search/searchServices/$SearchService"
)
if ($search.id -ine $expectedSearchId) {
    throw "Azure AI Search service $SearchService resolved outside its configured resource group."
}
$leaseStorage = & az storage account show `
    --resource-group $MutationLeaseResourceGroup `
    --name $MutationLeaseStorageAccount `
    --output json `
    --only-show-errors | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $leaseStorage.id) {
    throw "Mutation lease storage account $MutationLeaseStorageAccount is unavailable."
}
$expectedLeaseStorageId = (
    "/subscriptions/$subscriptionId/resourceGroups/$MutationLeaseResourceGroup" +
    "/providers/Microsoft.Storage/storageAccounts/$MutationLeaseStorageAccount"
)
if ($leaseStorage.id -ine $expectedLeaseStorageId) {
    throw (
        "Mutation lease storage account $MutationLeaseStorageAccount resolved outside " +
        "its configured resource group."
    )
}
$leaseBlob = & az storage blob exists `
    --auth-mode login `
    --account-name $MutationLeaseStorageAccount `
    --container-name $MutationLeaseContainer `
    --name $mutationLeaseBlobName `
    --output json `
    --only-show-errors | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $leaseBlob.exists -ne $true) {
    throw (
        "The pre-provisioned Foundry mutation lease blob is unavailable. " +
        "This script never creates shared storage or lock resources."
    )
}
$modelState = & az cognitiveservices account deployment show `
    --resource-group $ResourceGroup `
    --name $AccountName `
    --deployment-name $Model `
    --query properties.provisioningState `
    --output tsv `
    --only-show-errors
if ($LASTEXITCODE -ne 0 -or $modelState -ne "Succeeded") {
    throw "Model deployment $Model is not active."
}

if ($Rollback) {
    $rollbackPath = Join-Path $runRoot "rollback.json"
    Invoke-AgentIqPython -Arguments @(
        "-m", "agent_iq.deployments.foundry_host",
        "rollback",
        "--record", $rollbackRecordPath,
        "--account-name", $AccountName,
        "--project-name", $ProjectName,
        "--mutation-lease-blob-url", $mutationLeaseBlobUrl,
        "--output", $rollbackPath
    )
    Write-Host "Descriptor host rollback recorded at $rollbackPath" -ForegroundColor Green
    return
}

$runtimePath = Join-Path $root "src\agent_iq\data\consumer\runtime.json"
$runtime = (Get-Content $runtimePath -Raw | ConvertFrom-Json).agents |
    Where-Object { $_.agentId -eq $LogicalAgentId } |
    Select-Object -First 1
if (-not $runtime -or -not $runtime.knowledgeBindingId) {
    throw "Knowledge binding for $LogicalAgentId is unavailable."
}
$knowledgeEndpoint = (
    "https://$SearchService.search.windows.net" +
    "/knowledgebases/$($runtime.knowledgeBindingId)/mcp" +
    "?api-version=2026-05-01-preview"
)
$connectionName = "$LogicalAgentId-kb-mcp"
$azdProjectEndpoint = Assert-CanonicalFoundryProjectEndpoint `
    -Value $ProjectEndpoint `
    -AccountName $AccountName `
    -ProjectName $ProjectName
$connectionRaw = & azd ai connection show $connectionName `
    --project-endpoint $azdProjectEndpoint `
    --output json
if ($LASTEXITCODE -ne 0 -or -not $connectionRaw) {
    throw "Grounding connection $connectionName is unavailable."
}
$connectionBefore = $connectionRaw | ConvertFrom-Json
if ($connectionBefore.target -ne $knowledgeEndpoint) {
    throw "Grounding connection $connectionName targets an unexpected endpoint."
}
$expectedConnectionAuth = if ($GroundingAuthType -eq "project-managed-identity") {
    "ProjectManagedIdentity"
}
else {
    "AgenticIdentityToken"
}
if ($connectionBefore.authType -cne $expectedConnectionAuth) {
    throw (
        "Grounding connection $connectionName auth is incompatible. " +
        "The host canary never mutates shared connection state."
    )
}

$acrPullRole = switch ($acr.roleAssignmentMode) {
    "LegacyRegistryPermissions" { "AcrPull"; break }
    "AbacRepositoryPermissions" { "Container Registry Repository Reader"; break }
    default {
        throw "Unsupported ACR role assignment mode '$($acr.roleAssignmentMode)'."
    }
}
if ($GroundingAuthType -eq "project-managed-identity") {
    Require-Role `
        -ObjectId $project.identity.principalId `
        -Role "Search Index Data Reader" `
        -Scope $search.id
}
Require-Role `
    -ObjectId $project.identity.principalId `
    -Role $acrPullRole `
    -Scope $acr.id

$toolboxName = "$LogicalAgentId-knowledge"
$toolboxReport = Join-Path $runRoot "toolbox.json"
$pythonProjectEndpoint = Assert-CanonicalFoundryProjectEndpoint `
    -Value $ProjectEndpoint `
    -AccountName $AccountName `
    -ProjectName $ProjectName
Invoke-AgentIqPython -Arguments @(
    "-m", "agent_iq.deployments.foundry_host",
    "probe-toolbox",
    "--project-endpoint", $pythonProjectEndpoint,
    "--account-name", $AccountName,
    "--project-name", $ProjectName,
    "--toolbox-name", $toolboxName,
    "--output", $toolboxReport
)

$planPath = Join-Path $runRoot "plan.json"
$cardPath = Join-Path $runRoot "agent-card.json"
$operationId = [guid]::NewGuid().ToString("D")
$deploymentPath = Join-Path $runRoot "$operationId.deployment.json"

if ($ProbeOnly) {
    if (-not (Test-Path $latestPath)) {
        throw "ProbeOnly requires an existing deployment record at $latestPath."
    }
    $latest = Get-Content $latestPath -Raw | ConvertFrom-Json
    if (-not $latest.plan -or -not $latest.deployment) {
        throw "ProbeOnly requires a complete prior plan and deployment record."
    }
    $immutableImage = Resolve-VerifiedImmutableImage -ImageReference $latest.plan.image
    New-HostPlan -Image $immutableImage -OutputPath $planPath -CardOutputPath $cardPath
    $currentPlan = Get-Content $planPath -Raw | ConvertFrom-Json
    if (
        $currentPlan.logicalAgentId -cne $latest.plan.logicalAgentId -or
        $currentPlan.deploymentAgentId -cne $latest.plan.deploymentAgentId -or
        $currentPlan.projectEndpoint -cne $latest.plan.projectEndpoint -or
        $currentPlan.image -cne $latest.plan.image -or
        $currentPlan.modelDeployment -cne $latest.plan.modelDeployment -or
        $currentPlan.searchService -cne $latest.plan.searchService -or
        $currentPlan.runtimeDescriptor.descriptorSha256 -cne
            $latest.plan.runtimeDescriptor.descriptorSha256 -or
        ($currentPlan.agentCard | ConvertTo-Json -Depth 40 -Compress) -cne
            ($latest.plan.agentCard | ConvertTo-Json -Depth 40 -Compress)
    ) {
        throw "ProbeOnly detected drift from the certified descriptor host plan."
    }
    $latest.deployment | ConvertTo-Json -Depth 60 |
        Set-Content $deploymentPath -Encoding utf8
    $deployment = Get-Content $deploymentPath -Raw | ConvertFrom-Json
    $null = Assert-ExactAgentVersion `
        -Value $deployment.version `
        -Label "ProbeOnly deployment version"
    if ($deployment.image -cne $immutableImage) {
        throw "ProbeOnly deployment image does not match the verified immutable image."
    }
    Test-DescriptorImage -Image $immutableImage -PlanPath $planPath
    if ($connectionBefore.authType -ne $expectedConnectionAuth) {
        throw "ProbeOnly found grounding connection auth drift for $connectionName."
    }
    if (
        $GroundingAuthType -eq "project-managed-identity" -and
        -not (Test-Role `
            -ObjectId $project.identity.principalId `
            -Role "Search Index Data Reader" `
            -Scope $search.id)
    ) {
        throw "ProbeOnly found missing project Search read access."
    }
    if (-not (Test-Role `
        -ObjectId $project.identity.principalId `
        -Role $acrPullRole `
        -Scope $acr.id)) {
        throw "ProbeOnly found missing project ACR read access."
    }
    if ($GroundingAuthType -eq "agent-identity") {
        if (
            -not $deployment.instancePrincipalId -or
            -not (Test-Role `
                -ObjectId $deployment.instancePrincipalId `
                -Role "Search Index Data Reader" `
                -Scope $search.id)
        ) {
            throw "ProbeOnly found missing hosted-agent Search read access."
        }
    }
    $groundingConnection = [ordered]@{
        name = $connectionName
        target = $knowledgeEndpoint
        previousAuthType = $connectionBefore.authType
        activeAuthType = $connectionBefore.authType
        projectPrincipalId = if ($GroundingAuthType -eq "project-managed-identity") {
            $project.identity.principalId
        }
        else {
            $null
        }
        projectSearchRoleCreated = $false
        compatibilityReason = if ($GroundingAuthType -eq "project-managed-identity") {
            "Foundry Toolbox MCP currently drops agent identity context before connection resolution."
        }
        else {
            $null
        }
    }
    $containerRegistry = [ordered]@{
        name = $AcrName
        roleAssignmentMode = $acr.roleAssignmentMode
        pullRole = $acrPullRole
        projectPrincipalId = $project.identity.principalId
        roleCreated = $false
    }
    Invoke-HostCertification `
        -PlanPath $planPath `
        -CardPath $cardPath `
        -DeploymentPath $deploymentPath `
        -ToolboxReport $toolboxReport `
        -GroundingConnection $groundingConnection `
        -ContainerRegistry $containerRegistry
    return
}

if ($SkipBuild) {
    if (-not $ExistingImage) {
        throw "SkipBuild requires ExistingImage pinned with @sha256:<digest>."
    }
    $immutableImage = Resolve-VerifiedImmutableImage -ImageReference $ExistingImage
    New-HostPlan -Image $immutableImage -OutputPath $planPath -CardOutputPath $cardPath
    Test-DescriptorImage -Image $immutableImage -PlanPath $planPath
}
else {
    & az acr login --name $AcrName --only-show-errors | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker login to $AcrName failed."
    }
    $tag = $stamp.ToLowerInvariant()
    $taggedImage = "$($acr.loginServer)/${ImageRepository}:$tag"
    $dockerfile = Join-Path $root "deployments\foundry\host\Dockerfile"
    $buildMetadataPath = Join-Path $runRoot "image-build-metadata.json"
    Push-Location $root
    try {
        & docker buildx build `
            --platform linux/amd64 `
            --file $dockerfile `
            --tag $taggedImage `
            --metadata-file $buildMetadataPath `
            --load `
            .
        if ($LASTEXITCODE -ne 0) {
            throw "Descriptor host image build failed."
        }
    }
    finally {
        Pop-Location
    }
    if (-not (Test-Path $buildMetadataPath)) {
        throw "Descriptor host build did not emit immutable image metadata."
    }
    $buildMetadata = Get-Content $buildMetadataPath -Raw | ConvertFrom-Json
    $builtDigest = [string]$buildMetadata."containerimage.digest"
    if ($builtDigest -cnotmatch "^sha256:[0-9a-f]{64}$") {
        throw "Descriptor host build did not resolve an immutable image digest."
    }
    $immutableImage = "$($acr.loginServer)/${ImageRepository}@$builtDigest"
    New-HostPlan -Image $immutableImage -OutputPath $planPath -CardOutputPath $cardPath
    Test-DescriptorImage -Image $taggedImage -PlanPath $planPath

    & docker push $taggedImage
    if ($LASTEXITCODE -ne 0) {
        throw "Descriptor host image push failed."
    }
    $publishedDigest = Get-VerifiedManifestDigest `
        -ManifestName "${ImageRepository}:$tag" `
        -Description "newly published image"
    if ($publishedDigest -cne $builtDigest) {
        throw "The published image digest does not match the validated local build digest."
    }
    $verifiedImage = Resolve-VerifiedImmutableImage `
        -ImageReference "$($acr.loginServer)/${ImageRepository}@$publishedDigest"
    if ($verifiedImage -cne $immutableImage) {
        throw "The published immutable image reference does not match the validated host plan."
    }
    Test-DescriptorImage -Image $verifiedImage -PlanPath $planPath
}

# Mutation phase: every deployment prerequisite above has completed successfully.
$groundingConnection = [ordered]@{
    name = $connectionName
    target = $knowledgeEndpoint
    previousAuthType = $connectionBefore.authType
    activeAuthType = $connectionBefore.authType
    stateMode = "validated_read_only"
    projectPrincipalId = if ($GroundingAuthType -eq "project-managed-identity") {
        $project.identity.principalId
    }
    else {
        $null
    }
    projectSearchRoleCreated = $false
    compatibilityReason = if ($GroundingAuthType -eq "project-managed-identity") {
        "Foundry Toolbox MCP currently drops agent identity context before connection resolution."
    }
    else {
        $null
    }
}

$containerRegistry = [ordered]@{
    name = $AcrName
    roleAssignmentMode = $acr.roleAssignmentMode
    pullRole = $acrPullRole
    projectPrincipalId = $project.identity.principalId
    roleCreated = $false
}

Invoke-AgentIqPython -Arguments @(
    "-m", "agent_iq.deployments.foundry_host",
    "deploy",
    "--plan", $planPath,
    "--account-name", $AccountName,
    "--project-name", $ProjectName,
    "--mutation-lease-blob-url", $mutationLeaseBlobUrl,
    "--operation-id", $operationId,
    "--output", $deploymentPath
)
$deployment = Get-Content $deploymentPath -Raw | ConvertFrom-Json
$null = Assert-ExactAgentVersion `
    -Value $deployment.version `
    -Label "Created deployment version"
if (-not $deployment.instancePrincipalId) {
    throw "The hosted-agent identity was not returned by Foundry."
}

try {
    if ($GroundingAuthType -eq "agent-identity") {
        Require-Role `
            -ObjectId $deployment.instancePrincipalId `
            -Role "Search Index Data Reader" `
            -Scope $search.id
    }
    Invoke-HostCertification `
        -PlanPath $planPath `
        -CardPath $cardPath `
        -DeploymentPath $deploymentPath `
        -ToolboxReport $toolboxReport `
        -GroundingConnection $groundingConnection `
        -ContainerRegistry $containerRegistry
}
catch {
    $originalError = $_.Exception.Message
    $failureRollbackPath = Join-Path $runRoot "failure-rollback.json"
    try {
        Invoke-AgentIqPython -Arguments @(
            "-m", "agent_iq.deployments.foundry_host",
            "rollback",
            "--record", $deploymentPath,
            "--account-name", $AccountName,
            "--project-name", $ProjectName,
            "--mutation-lease-blob-url", $mutationLeaseBlobUrl,
            "--output", $failureRollbackPath
        )
        $failureRollback = Get-Content $failureRollbackPath -Raw | ConvertFrom-Json
    }
    catch {
        throw (
            "Descriptor host certification failed. Original error: $originalError " +
            "Exact traffic rollback also failed: $($_.Exception.Message)"
        )
    }
    if ($failureRollback.status -eq "retained") {
        throw (
            "Descriptor host certification failed. The isolated immutable version was " +
            "retained without compensating cloud mutation and requires operator review. " +
            "Original error: $originalError"
        )
    }
    throw (
        "Descriptor host certification failed and exact traffic rollback succeeded. " +
        "Original error: $originalError"
    )
}
