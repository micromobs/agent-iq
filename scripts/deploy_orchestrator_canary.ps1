[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ResourceGroup,
    [Parameter(Mandatory = $true)]
    [string]$ContainerAppsEnvironment,
    [Parameter(Mandatory = $true)]
    [string]$AcrName,
    [Parameter(Mandatory = $true)]
    [Alias("AzureAiAccount")]
    [string]$AiAccount,
    [Alias("ProjectName")]
    [string]$FoundryProject = $env:AGENT_IQ_AZURE_AI_PROJECT,
    [Parameter(Mandatory = $true)]
    [string]$SearchService,
    [string]$FoundryProjectEndpoint = $env:AGENT_IQ_FOUNDRY_PROJECT_ENDPOINT,
    [string]$AzureOpenAiEndpoint = "",
    [string]$EmbeddingDeployment = "text-embedding-3-large-gs",
    [string]$AppName = "agent-iq-orchestrator-canary",
    [string]$Repository = "agent-iq-orchestrator",
    [string]$AppInsightsName = "agent-iq-orchestrator-ai",
    [string]$WorkspaceName = "agent-iq-orchestrator-law",
    [string]$ImageTag = "",
    [string]$ArtifactCompanionApplicationId = (
        $env:AGENT_IQ_ARTIFACT_COMPANION_APPLICATION_ID
    ),
    [string]$ArtifactCompanionTenantId = (
        $env:AGENT_IQ_ARTIFACT_COMPANION_TENANT_ID
    ),
    [switch]$SkipBuild,
    [switch]$Bootstrap,
    [switch]$RollbackDrill,
    [switch]$Delete,
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = Split-Path -Parent $PSScriptRoot
$loginScript = Join-Path $PSScriptRoot "foundry_login.ps1"
$dockerfile = Join-Path $root "deployments\container-apps\orchestrator-canary\Dockerfile"
$reportRoot = Join-Path $root "reports\deployments\container-apps\orchestrator-canary"
$canaryNamePattern = "^agent-iq-orchestrator-canary(?:-[a-z0-9]+)?$"
$canaryOwnershipTags = [ordered]@{
    "agent-iq-owner" = "agent-iq"
    "agent-iq-component" = "orchestrator"
    "agent-iq-canary" = "true"
    "agent-iq-environment" = "canary"
    "agent-iq-purpose" = "canary"
    "agent-iq-managed-by" = "deploy_orchestrator_canary"
}
$canaryIngressConfiguration = [ordered]@{
    external = $true
    targetPort = 8080
    transport = "auto"
}
$canaryTagArguments = @(
    $canaryOwnershipTags.GetEnumerator() | ForEach-Object {
        "{0}={1}" -f $_.Key, $_.Value
    }
)
$reportRunPrefix = "orchestrator-canary-run-"
$maxDeploymentReports = 20

function Get-OptionalPropertyValue {
    param(
        [AllowNull()]
        [object]$Object,
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    if ($null -eq $Object) {
        return $null
    }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $null
    }
    return $property.Value
}

function Get-TagValue {
    param(
        [AllowNull()]
        [object]$Tags,
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    if ($null -eq $Tags) {
        return $null
    }
    if ($Tags -is [System.Collections.IDictionary]) {
        if ($Tags.Contains($Name)) {
            return [string]$Tags[$Name]
        }
        return $null
    }
    return [string](Get-OptionalPropertyValue -Object $Tags -Name $Name)
}

function Get-TagNames {
    param(
        [AllowNull()]
        [object]$Tags
    )

    if ($null -eq $Tags) {
        return @()
    }
    if ($Tags -is [System.Collections.IDictionary]) {
        return @($Tags.Keys | ForEach-Object { [string]$_ })
    }
    return @($Tags.PSObject.Properties.Name)
}

function ConvertTo-SafeAzureEndpoint {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value,
        [Parameter(Mandatory = $true)]
        [string]$DisplayName,
        [Parameter(Mandatory = $true)]
        [ValidateSet("FoundryProject", "Origin")]
        [string]$Kind
    )

    if (
        [string]::IsNullOrWhiteSpace($Value) -or
        $Value -cne $Value.Trim() -or
        $Value -match "[\x00-\x20\x7f]" -or
        $Value.Contains("\") -or
        $Value.Contains("@") -or
        $Value.Contains("?") -or
        $Value.Contains("#") -or
        $Value.Contains("%")
    ) {
        throw "$DisplayName contains credentials, delimiters, or unsafe characters."
    }

    $uri = $null
    if (
        -not [System.Uri]::TryCreate(
            $Value,
            [System.UriKind]::Absolute,
            [ref]$uri
        ) -or
        $null -eq $uri -or
        $uri.Scheme -cne "https" -or
        $uri.HostNameType -ne [System.UriHostNameType]::Dns -or
        [string]::IsNullOrWhiteSpace($uri.DnsSafeHost) -or
        -not [string]::IsNullOrEmpty($uri.UserInfo) -or
        -not [string]::IsNullOrEmpty($uri.Query) -or
        -not [string]::IsNullOrEmpty($uri.Fragment)
    ) {
        throw "$DisplayName must be a credential-free absolute HTTPS endpoint."
    }

    $path = $uri.AbsolutePath
    if ($Kind -eq "Origin") {
        if ($path -ne "/") {
            throw "$DisplayName must be an HTTPS origin without a path."
        }
        $canonicalPath = "/"
    }
    else {
        $canonicalPath = $path.TrimEnd("/")
        if ($canonicalPath -cnotmatch "^/api/projects/[A-Za-z0-9][A-Za-z0-9._-]*$") {
            throw (
                "$DisplayName must use the canonical " +
                "'/api/projects/<project-name>' path."
            )
        }
    }

    $authority = $uri.DnsSafeHost.ToLowerInvariant()
    if (-not $uri.IsDefaultPort) {
        $authority += ":$($uri.Port)"
    }
    return "https://$authority$canonicalPath"
}

function ConvertTo-CanonicalAzureAiAccount {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    if (
        [string]::IsNullOrWhiteSpace($Name) -or
        $Name -cne $Name.Trim() -or
        $Name.Length -gt 63 -or
        $Name -cnotmatch "^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
    ) {
        throw (
            "Azure AI account must be a canonical lowercase DNS label containing " +
            "only letters, numbers, and hyphens."
        )
    }
    return $Name
}

function ConvertTo-CanonicalAzureAiProject {
    param(
        [Parameter(Mandatory = $true)]
        [AllowNull()]
        [AllowEmptyString()]
        [string]$Name
    )

    if (
        [string]::IsNullOrWhiteSpace($Name) -or
        $Name -cne $Name.Trim() -or
        $Name.Length -gt 128 -or
        $Name -cnotmatch "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
    ) {
        throw (
            "Azure AI project must be a canonical resource name containing only " +
            "letters, numbers, periods, underscores, and hyphens."
        )
    }
    return $Name
}

function ConvertTo-CanonicalUuid {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value,
        [Parameter(Mandatory = $true)]
        [string]$DisplayName
    )

    $parsed = [Guid]::Empty
    if (
        [string]::IsNullOrWhiteSpace($Value) -or
        $Value -cne $Value.Trim() -or
        -not [Guid]::TryParseExact($Value, "D", [ref]$parsed) -or
        $parsed -eq [Guid]::Empty -or
        $Value -cne $parsed.ToString("D")
    ) {
        throw "$DisplayName must be a canonical non-zero UUID."
    }
    return $parsed.ToString("D")
}

function Resolve-BoundFoundryProjectEndpoint {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Account,
        [Parameter(Mandatory = $true)]
        [string]$Project,
        [AllowEmptyString()]
        [string]$Value = ""
    )

    $expectedEndpoint = (
        "https://$Account.services.ai.azure.com/api/projects/$Project"
    )
    if ([string]::IsNullOrEmpty($Value)) {
        return $expectedEndpoint
    }

    $validatedEndpoint = ConvertTo-SafeAzureEndpoint `
        -Value $Value `
        -DisplayName "Foundry project endpoint" `
        -Kind FoundryProject
    if ($Value -cne $expectedEndpoint -or $validatedEndpoint -cne $expectedEndpoint) {
        throw (
            "Foundry project endpoint must exactly match the configured Azure AI " +
            "account and project endpoint '$expectedEndpoint'."
        )
    }
    return $expectedEndpoint
}

function Resolve-BoundAzureOpenAiEndpoint {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Account,
        [AllowEmptyString()]
        [string]$Value = ""
    )

    $expectedEndpoint = "https://$Account.cognitiveservices.azure.com/"
    if ([string]::IsNullOrEmpty($Value)) {
        return $expectedEndpoint
    }

    $validatedEndpoint = ConvertTo-SafeAzureEndpoint `
        -Value $Value `
        -DisplayName "Azure OpenAI endpoint" `
        -Kind Origin
    if ($Value -cne $expectedEndpoint -or $validatedEndpoint -cne $expectedEndpoint) {
        throw (
            "Azure OpenAI endpoint must exactly match the Azure AI account origin " +
            "'$expectedEndpoint'."
        )
    }
    return $expectedEndpoint
}

function Assert-CanaryAppName {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    if (
        [string]::IsNullOrWhiteSpace($Name) -or
        $Name.Length -gt 32 -or
        $Name -cnotmatch $canaryNamePattern
    ) {
        throw (
            "Refusing to operate on AppName '$Name'. AppName must match the strict " +
            "Agent IQ orchestrator canary policy: agent-iq-orchestrator-canary " +
            "with at most one lowercase alphanumeric suffix."
        )
    }
}

function Assert-CanaryIdentity {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Resource
    )

    $resourceName = [string](Get-OptionalPropertyValue -Object $Resource -Name "name")
    if ($resourceName -cne $AppName) {
        throw (
            "Refusing to adopt Container App '$AppName': Azure returned resource " +
            "'$resourceName', which does not match the requested canary name."
        )
    }

    $tags = Get-OptionalPropertyValue -Object $Resource -Name "tags"
    if ($null -eq $tags) {
        throw (
            "Refusing to adopt Container App '$AppName': the existing resource has " +
            "no Agent IQ canary ownership tags."
        )
    }
    $actualTagNames = @(Get-TagNames -Tags $tags)
    $requiredTagNames = @($canaryOwnershipTags.Keys | ForEach-Object { [string]$_ })
    $unexpectedTagNames = @(
        $actualTagNames | Where-Object { $requiredTagNames -cnotcontains $_ }
    )
    $missingTagNames = @(
        $requiredTagNames | Where-Object { $actualTagNames -cnotcontains $_ }
    )
    if (
        $actualTagNames.Count -ne $requiredTagNames.Count -or
        $unexpectedTagNames.Count -gt 0 -or
        $missingTagNames.Count -gt 0
    ) {
        throw (
            "Refusing to adopt Container App '$AppName': ownership tag keys must " +
            "exactly equal the six required Agent IQ canary tags."
        )
    }
    foreach ($requiredTag in $canaryOwnershipTags.GetEnumerator()) {
        $actualValue = Get-TagValue -Tags $tags -Name $requiredTag.Key
        if ($actualValue -cne [string]$requiredTag.Value) {
            throw (
                "Refusing to adopt Container App '$AppName': ownership tag " +
                "'$($requiredTag.Key)' is missing or mismatched."
            )
        }
    }
    return $Resource
}

function Get-ContainerAppResource {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [string]$Group,
        [switch]$AllowNotFound
    )

    $commandOutput = @(
        & az containerapp show `
            --name $Name `
            --resource-group $Group `
            --output json `
            --only-show-errors 2>&1
    )
    $exitCode = $LASTEXITCODE
    $renderedOutput = ($commandOutput | ForEach-Object { [string]$_ }) -join "`n"
    $targetNotFound = (
        $renderedOutput -match "(?i)(container.?app|Microsoft\.App/containerApps)" -and
        $renderedOutput -match [regex]::Escape($Name) -and
        $renderedOutput -match "(?i)(not found|could not be found|was not found)"
    )
    if ($exitCode -ne 0) {
        if ($AllowNotFound -and $targetNotFound) {
            return $null
        }
        throw (
            "Unable to inspect Container App '$Name' in resource group '$Group'. " +
            $renderedOutput.Trim()
        )
    }
    if ([string]::IsNullOrWhiteSpace($renderedOutput)) {
        throw "Azure returned no metadata for Container App '$Name'."
    }
    try {
        $resource = $renderedOutput | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        throw (
            "Azure returned invalid metadata for Container App '$Name': " +
            $_.Exception.Message
        )
    }
    if ($null -eq $resource) {
        throw "Azure returned an empty resource for Container App '$Name'."
    }
    return $resource
}

function Assert-CanaryMutationAllowed {
    param(
        [switch]$ForCreate,
        [switch]$AllowMissing
    )

    if ($ForCreate -and $AllowMissing) {
        throw "The canary mutation guard cannot combine -ForCreate and -AllowMissing."
    }
    Assert-CanaryAppName -Name $AppName
    $resource = Get-ContainerAppResource `
        -Name $AppName `
        -Group $ResourceGroup `
        -AllowNotFound
    if ($null -eq $resource) {
        if ($ForCreate -or $AllowMissing) {
            return $null
        }
        throw (
            "Refusing Azure mutation: the Agent IQ orchestrator canary " +
            "'$AppName' does not exist."
        )
    }

    $null = Assert-CanaryIdentity -Resource $resource
    if ($ForCreate) {
        throw (
            "Refusing to create Agent IQ orchestrator canary '$AppName': " +
            "a resource with that name already exists."
        )
    }
    return $resource
}

function Get-CanaryRollbackState {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Resource
    )

    $properties = Get-OptionalPropertyValue -Object $Resource -Name "properties"
    $latestReadyRevision = [string](
        Get-OptionalPropertyValue -Object $properties -Name "latestReadyRevisionName"
    )
    $configuration = Get-OptionalPropertyValue -Object $properties -Name "configuration"
    $ingress = Get-OptionalPropertyValue -Object $configuration -Name "ingress"
    $traffic = @(
        (Get-OptionalPropertyValue -Object $ingress -Name "traffic") |
            Where-Object { $null -ne $_ }
    )
    $positiveTargets = @()
    foreach ($entry in $traffic) {
        $weightValue = Get-OptionalPropertyValue -Object $entry -Name "weight"
        $weight = 0
        if (
            $null -eq $weightValue -or
            -not [int]::TryParse([string]$weightValue, [ref]$weight) -or
            $weight -lt 0 -or
            $weight -gt 100
        ) {
            throw "Container Apps returned an invalid canary traffic weight."
        }
        if ($weight -eq 0) {
            continue
        }
        $label = [string](Get-OptionalPropertyValue -Object $entry -Name "label")
        $revisionName = [string](
            Get-OptionalPropertyValue -Object $entry -Name "revisionName"
        )
        if (-not [string]::IsNullOrWhiteSpace($label)) {
            throw "Label-based canary traffic is unsupported for the process-local runtime."
        }
        elseif (-not [string]::IsNullOrWhiteSpace($revisionName)) {
            $positiveTargets += [pscustomobject]@{
                revisionName = $revisionName
                weight = $weight
            }
        }
        elseif (
            [bool](Get-OptionalPropertyValue -Object $entry -Name "latestRevision") -and
            -not [string]::IsNullOrWhiteSpace($latestReadyRevision)
        ) {
            $positiveTargets += [pscustomobject]@{
                revisionName = $latestReadyRevision
                weight = $weight
            }
        }
        else {
            throw "Canary traffic does not resolve to an immutable revision."
        }
    }
    if ($positiveTargets.Count -eq 0 -and -not [string]::IsNullOrWhiteSpace($latestReadyRevision)) {
        $positiveTargets = @(
            [pscustomobject]@{
                revisionName = $latestReadyRevision
                weight = 100
            }
        )
    }
    if ($positiveTargets.Count -eq 0) {
        return [pscustomobject]@{
            available = $false
            revisionName = ""
            revisionMode = [string](
                Get-OptionalPropertyValue -Object $configuration -Name "activeRevisionsMode"
            )
            revisionWeights = @()
            labelWeights = @()
        }
    }
    if ($positiveTargets.Count -ne 1 -or [int]$positiveTargets[0].weight -ne 100) {
        throw (
            "The process-local canary requires exactly one immutable revision " +
            "to receive 100 percent of traffic."
        )
    }

    $servingRevision = [string]$positiveTargets[0].revisionName
    [pscustomobject]@{
        available = $true
        revisionName = $servingRevision
        revisionMode = [string](
            Get-OptionalPropertyValue -Object $configuration -Name "activeRevisionsMode"
        )
        revisionWeights = @("$servingRevision=100")
        labelWeights = @()
    }
}

function Assert-CanaryRevisionName {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RevisionName
    )

    $pattern = "^" + [regex]::Escape($AppName) + "--[a-z0-9][a-z0-9-]*$"
    if ($RevisionName -cnotmatch $pattern) {
        throw "Revision '$RevisionName' does not belong to canary '$AppName'."
    }
}

function Get-CanaryRevisionResource {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RevisionName
    )

    Assert-CanaryRevisionName -RevisionName $RevisionName
    $output = @(
        & az containerapp revision show `
            --name $AppName `
            --resource-group $ResourceGroup `
            --revision $RevisionName `
            --output json `
            --only-show-errors 2>$null
    )
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to resolve canary revision '$RevisionName'."
    }
    try {
        $revision = ($output -join [Environment]::NewLine) | ConvertFrom-Json
    }
    catch {
        throw "Azure returned malformed JSON for canary revision '$RevisionName'."
    }
    if ($null -eq $revision -or [string]$revision.name -cne $RevisionName) {
        throw "Azure returned the wrong canary revision for '$RevisionName'."
    }
    return $revision
}

function Assert-CanaryTrafficTarget {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RevisionName
    )

    Assert-CanaryRevisionName -RevisionName $RevisionName
    $resource = Assert-CanaryMutationAllowed
    $state = Get-CanaryRollbackState -Resource $resource
    if (
        -not [bool]$state.available -or
        [string]$state.revisionMode -cne "Multiple" -or
        [string]$state.revisionName -cne $RevisionName
    ) {
        throw "Canary traffic is not pinned 100 percent to revision '$RevisionName'."
    }
    return $resource
}

function Set-CanaryMultipleRevisionMode {
    $resource = Assert-CanaryMutationAllowed
    $properties = Get-OptionalPropertyValue -Object $resource -Name "properties"
    $configuration = Get-OptionalPropertyValue -Object $properties -Name "configuration"
    $mode = [string](
        Get-OptionalPropertyValue -Object $configuration -Name "activeRevisionsMode"
    )
    if ($mode -cne "Multiple") {
        $null = Assert-CanaryMutationAllowed
        & az containerapp revision set-mode `
            --name $AppName `
            --resource-group $ResourceGroup `
            --mode multiple `
            --only-show-errors *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to put the canary in multiple-revision deployment mode."
        }
    }

    $resource = Assert-CanaryMutationAllowed
    $properties = Get-OptionalPropertyValue -Object $resource -Name "properties"
    $configuration = Get-OptionalPropertyValue -Object $properties -Name "configuration"
    if (
        [string](
            Get-OptionalPropertyValue -Object $configuration -Name "activeRevisionsMode"
        ) -cne "Multiple"
    ) {
        throw "The canary did not enter multiple-revision deployment mode."
    }
    return $resource
}

function Set-CanaryTrafficTarget {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RevisionName
    )

    Assert-CanaryRevisionName -RevisionName $RevisionName
    $null = Assert-CanaryMutationAllowed
    & az containerapp ingress traffic set `
        --name $AppName `
        --resource-group $ResourceGroup `
        --revision-weight "$RevisionName=100" `
        --only-show-errors *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to route canary traffic to revision '$RevisionName'."
    }
    return Assert-CanaryTrafficTarget -RevisionName $RevisionName
}

function Restore-CanaryTraffic {
    param(
        [Parameter(Mandatory = $true)]
        [object]$RollbackState
    )

    if (-not [bool]$RollbackState.available) {
        return "Rollback skipped: no previously ready revision or traffic was captured."
    }

    $revisionName = [string]$RollbackState.revisionName
    if ([string]::IsNullOrWhiteSpace($revisionName)) {
        throw "Rollback failed: no previously serving revision was captured."
    }
    Assert-CanaryRevisionName -RevisionName $revisionName
    $revision = Get-CanaryRevisionResource -RevisionName $revisionName

    $resource = Assert-CanaryMutationAllowed
    $properties = Get-OptionalPropertyValue -Object $resource -Name "properties"
    $configuration = Get-OptionalPropertyValue -Object $properties -Name "configuration"
    if (
        [string](
            Get-OptionalPropertyValue -Object $configuration -Name "activeRevisionsMode"
        ) -cne "Multiple"
    ) {
        throw "Rollback failed: the canary is not in multiple-revision mode."
    }

    $revisionProperties = Get-OptionalPropertyValue -Object $revision -Name "properties"
    if (
        -not [bool](
            Get-OptionalPropertyValue -Object $revisionProperties -Name "active"
        )
    ) {
        $null = Assert-CanaryMutationAllowed
        & az containerapp revision activate `
            --name $AppName `
            --resource-group $ResourceGroup `
            --revision $revisionName `
            --only-show-errors *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to reactivate the previously serving revision '$revisionName'."
        }
    }

    $null = Set-CanaryTrafficTarget -RevisionName $revisionName
    return (
        "Rollback restored revision '$revisionName' with 100 percent Container Apps traffic."
    )
}

function Stop-CanaryRevision {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RevisionName
    )

    Assert-CanaryRevisionName -RevisionName $RevisionName
    $resource = Assert-CanaryMutationAllowed
    $serving = Get-CanaryRollbackState -Resource $resource
    if ([bool]$serving.available -and [string]$serving.revisionName -ceq $RevisionName) {
        throw "Refusing to deactivate serving canary revision '$RevisionName'."
    }
    $null = Assert-CanaryMutationAllowed
    & az containerapp revision deactivate `
        --name $AppName `
        --resource-group $ResourceGroup `
        --revision $RevisionName `
        --only-show-errors *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to deactivate non-serving canary revision '$RevisionName'."
    }
}

function Stop-StaleCanaryRevisions {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ServingRevision
    )

    $null = Assert-CanaryTrafficTarget -RevisionName $ServingRevision
    $revisions = @(
        & az containerapp revision list `
            --name $AppName `
            --resource-group $ResourceGroup `
            --output json `
            --only-show-errors | ConvertFrom-Json
    )
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect active canary revisions."
    }
    foreach ($revision in $revisions) {
        if (
            [bool]$revision.properties.active -and
            [string]$revision.name -cne $ServingRevision
        ) {
            Stop-CanaryRevision -RevisionName ([string]$revision.name)
        }
    }
}

function New-DeploymentReportPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Directory
    )

    do {
        $stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssfffffffZ")
        $runName = "$reportRunPrefix$stamp-$([Guid]::NewGuid().ToString('N')).json"
        $candidate = Join-Path $Directory $runName
    } while (Test-Path -LiteralPath $candidate)
    return $candidate
}

function Write-AtomicJsonFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Destination,
        [Parameter(Mandatory = $true)]
        [object]$Value
    )

    $directory = Split-Path -Parent $Destination
    $temporary = Join-Path `
        $directory `
        (".{0}.{1}.tmp" -f `
            [System.IO.Path]::GetFileName($Destination), `
            [Guid]::NewGuid().ToString("N"))
    try {
        $json = $Value | ConvertTo-Json -Depth 8
        $encoding = [System.Text.UTF8Encoding]::new($false)
        [System.IO.File]::WriteAllText($temporary, $json, $encoding)
        [System.IO.File]::Move($temporary, $Destination, $true)
    }
    finally {
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
        }
    }
}

function Remove-OldDeploymentReports {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Directory
    )

    $runPattern = "^" + [regex]::Escape($reportRunPrefix) +
        "\d{8}T\d{13}Z-[0-9a-f]{32}\.json$"
    $runFiles = @(
        Get-ChildItem -LiteralPath $Directory -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -cmatch $runPattern } |
            Sort-Object LastWriteTimeUtc, Name -Descending
    )
    foreach ($staleFile in @($runFiles | Select-Object -Skip $maxDeploymentReports)) {
        try {
            Remove-Item -LiteralPath $staleFile.FullName -Force -ErrorAction Stop
        }
        catch {
            Write-Warning (
                "Unable to remove stale canary deployment evidence '$($staleFile.FullName)': " +
                $_.Exception.Message
            )
        }
    }
}

function Confirm-ArtifactRouteFailsClosed {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BaseUrl
    )

    $body = @{
        artifact = @{
            schemaVersion = "1.0"
            kind = "demand_scenario"
            artifactId = "bootstrap-route-probe"
            productId = "brightwash-cold"
            market = "Germany"
            horizonStart = "2026-10-01"
            horizonEnd = "2026-10-31"
            unit = "cases"
            baselineQuantity = 1000
            forecastQuantity = 1250
            confidence = 0.84
            assumptions = @()
            evidenceIds = @("bootstrap-evidence")
            createdAt = "2026-07-13T00:00:00Z"
        }
    } | ConvertTo-Json -Depth 8 -Compress

    $response = Invoke-WebRequest `
        -Method Post `
        -Uri "$BaseUrl/v1/artifacts/publish" `
        -ContentType "application/json" `
        -Body $body `
        -MaximumRedirection 0 `
        -SkipHttpErrorCheck `
        -TimeoutSec 30
    $statusCode = [int]$response.StatusCode
    if ($statusCode -ne 401) {
        throw "Artifact route did not reject missing bearer credentials with HTTP 401."
    }

    $response = Invoke-WebRequest `
        -Method Post `
        -Uri "$BaseUrl/v1/artifacts/publish" `
        -Headers @{ Authorization = "Bearer invalid-probe-token" } `
        -ContentType "application/json" `
        -Body $body `
        -MaximumRedirection 0 `
        -SkipHttpErrorCheck `
        -TimeoutSec 30
    $statusCode = [int]$response.StatusCode
    if ($statusCode -ne 403) {
        throw "Artifact route did not reject a bearer token without a ticket with HTTP 403."
    }
}

function Confirm-McpContract {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BaseUrl
    )

    $python = Join-Path $root ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python)) {
        throw "The repository virtual environment is required for the MCP contract probe."
    }
    & $python (Join-Path $PSScriptRoot "probe_mcp_contract.py") `
        --mcp-url "$BaseUrl/mcp" `
        --timeout-seconds 60
    if ($LASTEXITCODE -ne 0) {
        throw "The public MCP handshake or tool/resource contract failed."
    }
}

function Confirm-CanaryContract {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BaseUrl,
        [switch]$AllowBootstrapNotReady
    )

    $healthy = $false
    $ready = $false
    $bootstrapNotReady = $false
    for ($attempt = 1; $attempt -le 30; $attempt++) {
        try {
            $healthResponse = Invoke-WebRequest `
                -Uri "$BaseUrl/healthz" `
                -Method Get `
                -MaximumRedirection 0 `
                -SkipHttpErrorCheck `
                -TimeoutSec 30
            if ($healthResponse.StatusCode -ne 200) {
                throw "Health endpoint returned HTTP $($healthResponse.StatusCode)."
            }
            $healthPayload = $healthResponse.Content | ConvertFrom-Json
            if ($healthPayload.status -ne "healthy") {
                throw "Health endpoint did not report healthy."
            }
            $healthy = $true

            $readyResponse = Invoke-WebRequest `
                -Uri "$BaseUrl/readyz" `
                -Method Get `
                -MaximumRedirection 0 `
                -SkipHttpErrorCheck `
                -TimeoutSec 30
            $readyStatusCode = [int]$readyResponse.StatusCode
            $readyPayload = $readyResponse.Content | ConvertFrom-Json
            if ($readyStatusCode -eq 200 -and $readyPayload.status -eq "ready") {
                $ready = $true
                break
            }
            if (
                $AllowBootstrapNotReady -and
                $readyStatusCode -eq 503 -and
                $readyPayload.status -eq "not_ready"
            ) {
                $bootstrapNotReady = $true
                break
            }
        }
        catch {
            $healthy = $false
        }
        if ($attempt -lt 30) {
            Start-Sleep -Seconds 10
        }
    }
    if (-not $healthy) {
        throw "The Agent IQ orchestrator candidate did not become healthy."
    }
    if (-not $ready -and -not ($AllowBootstrapNotReady -and $bootstrapNotReady)) {
        throw "The Agent IQ orchestrator candidate did not become ready."
    }

    $capabilitiesResponse = Invoke-WebRequest `
        -Uri "$BaseUrl/v1/capabilities" `
        -Method Get `
        -MaximumRedirection 0 `
        -SkipHttpErrorCheck `
        -TimeoutSec 30
    if ($capabilitiesResponse.StatusCode -ne 200) {
        throw "The deployed product capabilities route did not return HTTP 200."
    }
    $capabilities = $capabilitiesResponse.Content | ConvertFrom-Json
    if ($capabilities.artifactPublication -ne $true) {
        throw "The deployed product does not advertise artifact publication."
    }
    Confirm-ArtifactRouteFailsClosed -BaseUrl $BaseUrl
    Confirm-McpContract -BaseUrl $BaseUrl | Out-Host
    return $ready
}

function Confirm-CanaryRevisionImage {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RevisionName,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedImage,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedFqdn
    )

    $revision = Get-CanaryRevisionResource -RevisionName $RevisionName
    $properties = Get-OptionalPropertyValue -Object $revision -Name "properties"
    $fqdn = [string](Get-OptionalPropertyValue -Object $properties -Name "fqdn")
    $template = Get-OptionalPropertyValue -Object $properties -Name "template"
    $containers = @(
        (Get-OptionalPropertyValue -Object $template -Name "containers") |
            Where-Object { $null -ne $_ }
    )
    if ($fqdn -cne $ExpectedFqdn) {
        throw "Candidate revision FQDN does not match the deployment-bound probe origin."
    }
    if (
        $containers.Count -ne 1 -or
        [string](Get-OptionalPropertyValue -Object $containers[0] -Name "image") -cne $ExpectedImage
    ) {
        throw "Candidate revision does not run the verified immutable image."
    }
    return $revision
}

$AiAccount = ConvertTo-CanonicalAzureAiAccount -Name $AiAccount
$FoundryProject = ConvertTo-CanonicalAzureAiProject -Name $FoundryProject
$FoundryProjectEndpoint = Resolve-BoundFoundryProjectEndpoint `
    -Account $AiAccount `
    -Project $FoundryProject `
    -Value $FoundryProjectEndpoint
$AzureOpenAiEndpoint = Resolve-BoundAzureOpenAiEndpoint `
    -Account $AiAccount `
    -Value $AzureOpenAiEndpoint
Assert-CanaryAppName -Name $AppName
if ($Bootstrap -and $Delete) {
    throw "-Bootstrap cannot be combined with -Delete."
}
if ($RollbackDrill -and ($Bootstrap -or $Delete)) {
    throw "-RollbackDrill cannot be combined with -Bootstrap or -Delete."
}

if ($SkipBuild -and [string]::IsNullOrWhiteSpace($ImageTag)) {
    throw "-SkipBuild requires the exact existing -ImageTag."
}
if (-not $ImageTag) {
    $ImageTag = "d12-command-center-" + [DateTime]::UtcNow.ToString("yyyyMMddHHmmss")
}

if ($WhatIf) {
    [ordered]@{
        appName = $AppName
        resourceGroup = $ResourceGroup
        environment = $ContainerAppsEnvironment
        acr = $AcrName
        repository = $Repository
        imageTag = $ImageTag
        projectEndpoint = $FoundryProjectEndpoint
        azureAiAccount = $AiAccount
        azureAiProject = $FoundryProject
        azureOpenAiEndpoint = $AzureOpenAiEndpoint
        searchService = $SearchService
        anonymousCanary = $true
        bootstrap = [bool]$Bootstrap
        rollbackDrill = [bool]$RollbackDrill
        revisionStrategy = "candidate_probe_then_atomic_traffic"
        artifactCompanionApplicationId = $ArtifactCompanionApplicationId
        artifactCompanionTenantId = $ArtifactCompanionTenantId
        ownershipTags = $canaryOwnershipTags
        minReplicas = 1
        maxReplicas = 1
    } | ConvertTo-Json
}
else {
    . $loginScript -Quiet
    if ([string]::IsNullOrWhiteSpace($ArtifactCompanionApplicationId)) {
        $ArtifactCompanionApplicationId = $env:AZURE_CLIENT_ID
    }
    if ([string]::IsNullOrWhiteSpace($ArtifactCompanionTenantId)) {
        $ArtifactCompanionTenantId = $env:AZURE_TENANT_ID
    }
    $ArtifactCompanionApplicationId = ConvertTo-CanonicalUuid `
        -Value $ArtifactCompanionApplicationId `
        -DisplayName "Artifact companion application ID"
    $ArtifactCompanionTenantId = ConvertTo-CanonicalUuid `
        -Value $ArtifactCompanionTenantId `
        -DisplayName "Artifact companion tenant ID"
    if ($ArtifactCompanionTenantId -cne $env:AZURE_TENANT_ID) {
        throw "Artifact companion tenant ID must match the authenticated deployment tenant."
    }
    $artifactAudiencePrincipal = & az ad sp show `
        --id $ArtifactCompanionApplicationId `
        --query "{appId:appId,appRoleAssignmentRequired:appRoleAssignmentRequired}" `
        --output json `
        --only-show-errors | ConvertFrom-Json
    if (
        $LASTEXITCODE -ne 0 -or
        $null -eq $artifactAudiencePrincipal -or
        $artifactAudiencePrincipal.appId -cne $ArtifactCompanionApplicationId
    ) {
        throw "Artifact companion application service principal is unavailable in this tenant."
    }
    if ($artifactAudiencePrincipal.appRoleAssignmentRequired -eq $true) {
        throw (
            "Artifact companion application requires app-role assignment; the hosted-agent " +
            "managed identities could not use the ACL-bound companion channel."
        )
    }

    function Resolve-ImageDigest {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Registry,
        [Parameter(Mandatory = $true)]
        [string]$RepositoryName,
        [Parameter(Mandatory = $true)]
        [string]$Tag
    )

    $metadata = @(
        & az acr manifest show-metadata `
            --registry $Registry `
            --name "$RepositoryName`:$Tag" `
            --query digest `
            --output tsv `
            --only-show-errors 2>&1
    )
    $exitCode = $LASTEXITCODE
    $digest = ($metadata -join [Environment]::NewLine).Trim()
    if ($exitCode -ne 0 -or [string]::IsNullOrWhiteSpace($digest)) {
        throw "The requested ACR image tag '$RepositoryName`:$Tag' does not exist."
    }
    if ($digest -notmatch "^sha256:[0-9a-fA-F]{64}$") {
        throw "ACR returned a malformed sha256 digest for '$RepositoryName`:$Tag'."
    }
    return $digest.ToLowerInvariant()
    }

    if ($Delete) {
    $deleteTarget = Assert-CanaryMutationAllowed -AllowMissing
    if ($null -ne $deleteTarget) {
        $null = Assert-CanaryMutationAllowed
        & az containerapp delete `
            --name $AppName `
            --resource-group $ResourceGroup `
            --yes `
            --only-show-errors
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to delete the Agent IQ orchestrator canary."
        }
    }
    Write-Host "Agent IQ orchestrator canary deleted." -ForegroundColor Green
    return
    }

    $existing = Assert-CanaryMutationAllowed -AllowMissing
    $hadExistingCanary = $null -ne $existing
    $rollbackState = $null
    if ($null -ne $existing) {
        $rollbackState = Get-CanaryRollbackState -Resource $existing
    }
    if ($RollbackDrill -and $null -eq $rollbackState) {
        throw "-RollbackDrill requires an existing canary with one serving revision."
    }

    $loginServer = & az acr show `
    --name $AcrName `
    --resource-group $ResourceGroup `
    --query loginServer `
    --output tsv `
    --only-show-errors
if ($LASTEXITCODE -ne 0 -or -not $loginServer) {
    throw "Unable to resolve the configured Azure Container Registry."
}
$image = "$loginServer/$Repository`:$ImageTag"

if (-not $SkipBuild) {
    & docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker is unavailable. Start Docker Desktop and retry."
    }
    & az acr login --name $AcrName --only-show-errors
    if ($LASTEXITCODE -ne 0) {
        throw "Keyless Azure Container Registry login failed."
    }
    & docker build `
        --file $dockerfile `
        --tag $image `
        $root
    if ($LASTEXITCODE -ne 0) {
        throw "Agent IQ orchestrator image build failed."
    }
    & docker push $image
    if ($LASTEXITCODE -ne 0) {
        throw "Agent IQ orchestrator image push failed."
    }
}

$imageDigest = Resolve-ImageDigest `
    -Registry $AcrName `
    -RepositoryName $Repository `
    -Tag $ImageTag
$immutableImage = "$loginServer/$Repository@$imageDigest"

$environmentDomain = & az containerapp env show `
    --name $ContainerAppsEnvironment `
    --resource-group $ResourceGroup `
    --query properties.defaultDomain `
    --output tsv `
    --only-show-errors
if ($LASTEXITCODE -ne 0 -or -not $environmentDomain) {
    throw "Unable to resolve the Container Apps environment domain."
}
$publicBaseUrl = "https://$AppName.$environmentDomain"
$deploymentOperationId = [Guid]::NewGuid().ToString("N").Substring(0, 12)
$candidateRevisionSuffix = "candidate-$deploymentOperationId"
$candidateRevisionName = "$AppName--$candidateRevisionSuffix"
$candidateFqdn = "$candidateRevisionName.$environmentDomain"
$candidateBaseUrl = "https://$candidateFqdn"

if ($null -eq $existing) {
    $null = Assert-CanaryMutationAllowed -ForCreate
    & az containerapp create `
        --name $AppName `
        --resource-group $ResourceGroup `
        --environment $ContainerAppsEnvironment `
        --image $immutableImage `
        --ingress external `
        --target-port 80 `
        --system-assigned `
        --min-replicas 1 `
        --max-replicas 3 `
        --revisions-mode multiple `
        --revision-suffix "shell-$deploymentOperationId" `
        --tags $canaryTagArguments `
        --only-show-errors
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to create the isolated Agent IQ canary shell."
    }
    $existing = Assert-CanaryMutationAllowed
}

$principalId = & az containerapp identity show `
    --name $AppName `
    --resource-group $ResourceGroup `
    --query principalId `
    --output tsv `
    --only-show-errors
if ($LASTEXITCODE -ne 0 -or -not $principalId) {
    throw "Unable to resolve the canary managed identity."
}

$acrId = & az acr show `
    --name $AcrName `
    --resource-group $ResourceGroup `
    --query id `
    --output tsv `
    --only-show-errors
$accountId = & az cognitiveservices account show `
    --name $AiAccount `
    --resource-group $ResourceGroup `
    --query id `
    --output tsv `
    --only-show-errors
$searchId = & az search service show `
    --name $SearchService `
    --resource-group $ResourceGroup `
    --query id `
    --output tsv `
    --only-show-errors
if (-not $acrId -or -not $accountId -or -not $searchId) {
    throw "Unable to resolve one or more managed-identity role scopes."
}

function Ensure-Role {
    param(
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)][string]$Scope
    )
    $assignment = & az role assignment list `
        --assignee-object-id $principalId `
        --scope $Scope `
        --role $Role `
        --query "[0].id" `
        --output tsv `
        --only-show-errors
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect the '$Role' role assignment for the canary identity."
    }
    if (-not $assignment) {
        $null = Assert-CanaryMutationAllowed
        & az role assignment create `
            --assignee-object-id $principalId `
            --assignee-principal-type ServicePrincipal `
            --role $Role `
            --scope $Scope `
            --only-show-errors *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to assign role '$Role' to the canary identity."
        }
    }
}

Ensure-Role -Role "AcrPull" -Scope $acrId
Ensure-Role -Role "Cognitive Services User" -Scope $accountId
Ensure-Role -Role "Cognitive Services OpenAI User" -Scope $accountId
Ensure-Role -Role "Search Index Data Reader" -Scope $searchId

$location = & az group show `
    --name $ResourceGroup `
    --query location `
    --output tsv `
    --only-show-errors
$workspaceId = & az monitor log-analytics workspace show `
    --workspace-name $WorkspaceName `
    --resource-group $ResourceGroup `
    --query id `
    --output tsv `
    --only-show-errors 2>$null
if ($LASTEXITCODE -ne 0 -or -not $workspaceId) {
    $null = Assert-CanaryMutationAllowed
    $workspaceId = & az monitor log-analytics workspace create `
        --workspace-name $WorkspaceName `
        --resource-group $ResourceGroup `
        --location $location `
        --query id `
        --output tsv `
        --only-show-errors
}
if (-not $workspaceId) {
    throw "Unable to provision the canary Log Analytics workspace."
}

$appInsights = & az monitor app-insights component show `
    --app $AppInsightsName `
    --resource-group $ResourceGroup `
    --query name `
    --output tsv `
    --only-show-errors 2>$null
if ($LASTEXITCODE -ne 0 -or -not $appInsights) {
    $null = Assert-CanaryMutationAllowed
    & az monitor app-insights component create `
        --app $AppInsightsName `
        --resource-group $ResourceGroup `
        --location $location `
        --workspace $workspaceId `
        --only-show-errors *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to provision canary Application Insights."
    }
}
$appInsightsConnection = & az monitor app-insights component show `
    --app $AppInsightsName `
    --resource-group $ResourceGroup `
    --query connectionString `
    --output tsv `
    --only-show-errors
if (-not $appInsightsConnection) {
    throw "Unable to resolve the canary telemetry connection."
}

$candidateCreated = $false
$promotionCompleted = $false
$rollbackCompleted = $false
$ready = $false
$stagingRevision = ""
try {
    $existing = Set-CanaryMultipleRevisionMode
    $stagingState = Get-CanaryRollbackState -Resource $existing
    if (-not [bool]$stagingState.available) {
        throw "The canary has no revision that can safely hold traffic during deployment."
    }
    $stagingRevision = [string]$stagingState.revisionName
    if (
        $hadExistingCanary -and
        $null -ne $rollbackState -and
        [string]$rollbackState.revisionName -cne $stagingRevision
    ) {
        throw "The serving canary revision changed before deployment could begin."
    }
    $null = Set-CanaryTrafficTarget -RevisionName $stagingRevision
    if ($hadExistingCanary) {
        $rollbackState = Get-CanaryRollbackState -Resource (
            Assert-CanaryMutationAllowed
        )
    }
    Stop-StaleCanaryRevisions -ServingRevision $stagingRevision

    $null = Assert-CanaryMutationAllowed
    & az containerapp registry set `
        --name $AppName `
        --resource-group $ResourceGroup `
        --server $loginServer `
        --identity system `
        --only-show-errors
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to configure managed-identity ACR pull."
    }

    $null = Assert-CanaryMutationAllowed
    & az containerapp secret set `
        --name $AppName `
        --resource-group $ResourceGroup `
        --secrets "appinsights=$appInsightsConnection" `
        --only-show-errors *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inject the canary telemetry connection."
    }

    $environmentVariables = @(
        "AGENT_IQ_ENVIRONMENT=canary",
        "AGENT_IQ_NATIVE_A2A_REQUIRED=true",
        "AGENT_IQ_ALLOW_ANONYMOUS_CONNECTOR=true",
        "AGENT_IQ_FOUNDRY_PROJECT_ENDPOINT=$FoundryProjectEndpoint",
        "AGENT_IQ_AZURE_AI_ACCOUNT=$AiAccount",
        "AGENT_IQ_AZURE_AI_PROJECT=$FoundryProject",
        "AGENT_IQ_AZURE_OPENAI_ENDPOINT=$AzureOpenAiEndpoint",
        "AGENT_IQ_AZURE_EMBEDDING_DEPLOYMENT=$EmbeddingDeployment",
        "AGENT_IQ_SEARCH_SERVICE=$SearchService",
        "AGENT_IQ_PUBLIC_BASE_URL=$publicBaseUrl",
        "AGENT_IQ_DEPLOYMENT_PROBE_BASE_URL=$candidateBaseUrl",
        "AGENT_IQ_ARTIFACT_PUBLISH_ENDPOINT=$publicBaseUrl/v1/artifacts/publish",
        (
            "AGENT_IQ_ARTIFACT_COMPANION_APPLICATION_ID=" +
            $ArtifactCompanionApplicationId
        ),
        "AGENT_IQ_ARTIFACT_COMPANION_TENANT_ID=$ArtifactCompanionTenantId",
        "AGENT_IQ_ARTIFACT_TOKEN_SCOPE=$ArtifactCompanionApplicationId/.default",
        "AGENT_IQ_CANARY_AUDIENCE=$ArtifactCompanionApplicationId",
        "AGENT_IQ_LOG_LEVEL=INFO",
        "APPLICATIONINSIGHTS_CONNECTION_STRING=secretref:appinsights"
    )

    $null = Assert-CanaryMutationAllowed
    & az containerapp update `
        --name $AppName `
        --resource-group $ResourceGroup `
        --image $immutableImage `
        --min-replicas 1 `
        --max-replicas 1 `
        --revisions-mode multiple `
        --revision-suffix $candidateRevisionSuffix `
        --set-env-vars @environmentVariables `
        --only-show-errors
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to deploy the Agent IQ orchestrator image."
    }
    $candidateCreated = $true
    $null = Assert-CanaryTrafficTarget -RevisionName $stagingRevision

    $null = Assert-CanaryMutationAllowed
    & az containerapp ingress update `
        --name $AppName `
        --resource-group $ResourceGroup `
        --target-port 8080 `
        --transport auto `
        --type external `
        --only-show-errors *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to configure the Agent IQ orchestrator ingress."
    }

    $null = Assert-CanaryTrafficTarget -RevisionName $stagingRevision
    $null = Confirm-CanaryRevisionImage `
        -RevisionName $candidateRevisionName `
        -ExpectedImage $immutableImage `
        -ExpectedFqdn $candidateFqdn
    $ready = Confirm-CanaryContract `
        -BaseUrl $candidateBaseUrl `
        -AllowBootstrapNotReady:$Bootstrap
    $null = Assert-CanaryTrafficTarget -RevisionName $stagingRevision

    $null = Set-CanaryTrafficTarget -RevisionName $candidateRevisionName
    $promotionCompleted = $true
    $ready = Confirm-CanaryContract `
        -BaseUrl $publicBaseUrl `
        -AllowBootstrapNotReady:$Bootstrap
    $null = Assert-CanaryTrafficTarget -RevisionName $candidateRevisionName
    Stop-CanaryRevision -RevisionName $stagingRevision

    if ($RollbackDrill) {
        $null = Restore-CanaryTraffic -RollbackState $rollbackState
        $rollbackCompleted = $true
        $null = Confirm-CanaryContract -BaseUrl $publicBaseUrl
        $null = Assert-CanaryTrafficTarget -RevisionName $stagingRevision
        Stop-CanaryRevision -RevisionName $candidateRevisionName
    }
}
catch {
    $originalError = $_.Exception
    $rollbackError = $null
    $rollbackMessage = "Rollback was not required before candidate creation."
    $rollbackSucceeded = $false
    if (
        $candidateCreated -and
        $null -ne $rollbackState -and
        [bool]$rollbackState.available
    ) {
        try {
            $rollbackMessage = Restore-CanaryTraffic -RollbackState $rollbackState
            $rollbackSucceeded = $true
            $rollbackCompleted = $true
        }
        catch {
            $rollbackError = $_.Exception
            $rollbackMessage = "Rollback failed: $($rollbackError.Message)"
        }
    }
    if ($candidateCreated -and $rollbackSucceeded) {
        try {
            Stop-CanaryRevision -RevisionName $candidateRevisionName
            $rollbackMessage += " The failed candidate revision was deactivated."
        }
        catch {
            $rollbackError = $_.Exception
            $rollbackMessage += (
                " Candidate cleanup failed: $($rollbackError.Message)"
            )
        }
    }

    $failureMessage = (
        "Agent IQ orchestrator canary deployment failed. " +
        "Original error: $($originalError.Message). $rollbackMessage"
    )
    if ($null -ne $rollbackError) {
        $failureMessage += " Rollback error: $($rollbackError.Message)."
    }
    $combinedError = [System.InvalidOperationException]::new(
        $failureMessage,
        $originalError
    )
    if ($null -ne $rollbackError) {
        $combinedError.Data["RollbackError"] = $rollbackError.ToString()
    }
    throw $combinedError
}

$currentResource = Assert-CanaryMutationAllowed
$currentState = Get-CanaryRollbackState -Resource $currentResource
if (
    -not [bool]$currentState.available -or
    [string]$currentState.revisionMode -cne "Multiple"
) {
    throw "The current canary resource has no immutable 100-percent traffic target."
}
$revision = [string]$currentState.revisionName
$activeRevisions = @(
    & az containerapp revision list `
        --name $AppName `
        --resource-group $ResourceGroup `
        --query "[?properties.active].name" `
        --output tsv `
        --only-show-errors
)
if ($LASTEXITCODE -ne 0) {
    throw "Unable to record the active canary revisions."
}
$activeRevisions = @(
    $activeRevisions |
        ForEach-Object { [string]$_ } |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
)
$rollbackReady = $false
$previousRevision = $null
$previousActive = $null
if (-not $RollbackDrill -and $hadExistingCanary -and $null -ne $rollbackState) {
    $previousRevision = Get-CanaryRevisionResource `
        -RevisionName ([string]$rollbackState.revisionName)
    $previousProperties = Get-OptionalPropertyValue `
        -Object $previousRevision `
        -Name "properties"
    $previousActive = [bool](
        Get-OptionalPropertyValue -Object $previousProperties -Name "active"
    )
    $rollbackReady = (
        -not $previousActive -and
        [string](
            Get-OptionalPropertyValue -Object $previousProperties -Name "healthState"
        ) -ceq "Healthy" -and
        [string](
            Get-OptionalPropertyValue -Object $previousProperties -Name "provisioningState"
        ) -ceq "Provisioned"
    )
}

New-Item -ItemType Directory -Force -Path $reportRoot | Out-Null
$report = [ordered]@{
    schemaVersion = "1.0"
    generatedAt = [DateTime]::UtcNow.ToString("o")
    status = "pass"
    deploymentPhase = $(
        if ($RollbackDrill) { "rollback-drill" }
        elseif ($Bootstrap) { "bootstrap" }
        else { "final" }
    )
    productReady = $ready
    appName = $AppName
    resourceGroup = $ResourceGroup
    fqdn = $publicBaseUrl
    revision = $revision
    revisionMode = "Multiple"
    revisionStrategy = "candidate_probe_then_atomic_traffic"
    candidateRevision = $candidateRevisionName
    candidateProbeBaseUrl = $candidateBaseUrl
    previousRevision = $(
        if ($null -ne $rollbackState) { [string]$rollbackState.revisionName }
        else { $null }
    )
    activeRevisions = $activeRevisions
    candidateValidatedBeforeTraffic = $true
    trafficPromotionVerified = $promotionCompleted
    rollbackDrill = [bool]$RollbackDrill
    rollbackVerified = $rollbackCompleted
    rollbackReady = $rollbackReady
    rollbackRevisionActive = $(
        if ($null -ne $previousRevision) { $previousActive }
        else { $null }
    )
    rollbackActivationRequired = $(
        if ($null -ne $previousRevision) { -not $previousActive }
        else { $null }
    )
    image = $immutableImage
    imageTag = $ImageTag
    imageTagReference = $image
    imageDigest = $imageDigest
    ownershipTags = $canaryOwnershipTags
    managedIdentityObjectId = $principalId
    artifactCompanionApplicationId = $ArtifactCompanionApplicationId
    artifactCompanionTenantId = $ArtifactCompanionTenantId
    artifactTokenAudience = $ArtifactCompanionApplicationId
    artifactTokenScope = "$ArtifactCompanionApplicationId/.default"
    ingress = @("mcp", "http_sse", "a2a")
    containerAppIngress = $canaryIngressConfiguration
    authentication = "anonymous_canary"
    containerAppAuthConfig = "none"
    applicationInsights = $AppInsightsName
    minReplicas = 1
    maxReplicas = 1
}
$reportPath = New-DeploymentReportPath -Directory $reportRoot
$latestPath = Join-Path `
    $reportRoot `
    $(if ($RollbackDrill) { "latest-rollback-drill.json" } else { "latest.json" })
Write-AtomicJsonFile -Destination $reportPath -Value $report
Write-AtomicJsonFile -Destination $latestPath -Value $report
Remove-OldDeploymentReports -Directory $reportRoot

    $phaseStatus = if ($ready) { "ready" } else { "bootstrap-ready" }
    if ($RollbackDrill) {
        Write-Host (
            "Agent IQ orchestrator rollback drill passed; serving revision " +
            "'$revision' was restored."
        ) -ForegroundColor Green
    }
    else {
        Write-Host "Agent IQ orchestrator canary $phaseStatus`: $publicBaseUrl" -ForegroundColor Green
    }
    Write-Host "Deployment image: $immutableImage (tag $ImageTag; digest $imageDigest)"
    Write-Host "Deployment evidence: $latestPath"
}
