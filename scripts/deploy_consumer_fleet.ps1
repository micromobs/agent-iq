[CmdletBinding()]
param(
    [string[]]$Only = @(),
    [string]$ProjectEndpoint = "",
    [string]$ResourceGroup = $env:AGENT_IQ_RESOURCE_GROUP,
    [string]$AccountName = $env:AGENT_IQ_AZURE_AI_ACCOUNT,
    [string]$ProjectName = $env:AGENT_IQ_AZURE_AI_PROJECT,
    [string]$SearchResourceGroup = $env:AGENT_IQ_SEARCH_RESOURCE_GROUP,
    [string]$SearchService = $env:AGENT_IQ_SEARCH_SERVICE,
    [string]$Model = "gpt-5.4-mini",
    [string]$OrchestratorBaseUrl = $env:AGENT_IQ_ORCHESTRATOR_BASE_URL,
    [string]$OrchestratorAppName = "agent-iq-orchestrator-canary",
    [string]$CompanionApplicationId = (
        $env:AGENT_IQ_ARTIFACT_COMPANION_APPLICATION_ID
    ),
    [string]$CompanionTenantId = (
        $env:AGENT_IQ_ARTIFACT_COMPANION_TENANT_ID
    ),
    [string]$ArtifactRoot = (
        Join-Path $env:LOCALAPPDATA "AgentIQ\foundry\consumer"
    ),
    [string]$WorkRoot = (
        Join-Path $env:LOCALAPPDATA "AgentIQ\foundry\work"
    ),
    [string]$ReportRoot = "",
    [int]$Runs = 1,
    [switch]$ProbeOnly,
    [switch]$SkipProbe,
    [switch]$Rollback,
    [string]$RollbackRecord = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

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

function ConvertTo-TrustedOrchestratorCanaryBaseUrl {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value,
        [Parameter(Mandatory = $true)]
        [string]$AppName
    )

    if (
        [string]::IsNullOrWhiteSpace($Value) -or
        $Value -cne $Value.Trim() -or
        $Value -match "[\x00-\x20\x7f]" -or
        $Value.Contains("\") -or
        $Value.Contains("%") -or
        $Value.Contains("@") -or
        $Value.Contains("?") -or
        $Value.Contains("#")
    ) {
        throw "Artifact companion base URL contains unsafe or noncanonical characters."
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
        -not [string]::IsNullOrEmpty($uri.UserInfo) -or
        -not [string]::IsNullOrEmpty($uri.Query) -or
        -not [string]::IsNullOrEmpty($uri.Fragment) -or
        -not $uri.IsDefaultPort -or
        $uri.AbsolutePath -cne "/"
    ) {
        throw (
            "Artifact companion base URL must be a credential-free HTTPS origin " +
            "without a port, query, or fragment."
        )
    }

    $hostName = $uri.DnsSafeHost.ToLowerInvariant()
    if ($AppName -cnotmatch "^agent-iq-orchestrator-canary(?:-[a-z0-9]+)?$") {
        throw "Artifact companion application name is not an Agent IQ canary name."
    }
    $escapedAppName = [regex]::Escape($AppName)
    if (
        $hostName -cnotmatch (
            "^$escapedAppName\." +
            "[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\." +
            "[a-z0-9]+\.azurecontainerapps\.io$"
        )
    ) {
        throw (
            "Artifact companion must be the Agent IQ orchestrator canary on the " +
            "approved Azure Container Apps host."
        )
    }
    $canonical = "https://$hostName"
    if ($Value -cne $canonical) {
        throw "Artifact companion base URL is not canonical."
    }
    return $canonical
}

function Resolve-TrustedArtifactChannel {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BaseUrl,
        [Parameter(Mandatory = $true)]
        [string]$AppName,
        [Parameter(Mandatory = $true)]
        [string]$ApplicationId,
        [Parameter(Mandatory = $true)]
        [string]$TenantId
    )

    $canonicalBaseUrl = ConvertTo-TrustedOrchestratorCanaryBaseUrl `
        -Value $BaseUrl `
        -AppName $AppName
    $applicationGuid = [guid]::Empty
    if (
        -not [guid]::TryParseExact($ApplicationId, "D", [ref]$applicationGuid) -or
        $applicationGuid -eq [guid]::Empty -or
        $ApplicationId -cne $applicationGuid.ToString("D")
    ) {
        throw (
            "Artifact companion application ID must be a canonical immutable " +
            "Entra application/client UUID."
        )
    }
    $tenantGuid = [Guid]::Empty
    if (
        -not [Guid]::TryParseExact($TenantId, "D", [ref]$tenantGuid) -or
        $tenantGuid -eq [Guid]::Empty -or
        $TenantId -cne $tenantGuid.ToString("D")
    ) {
        throw "CompanionTenantId must be a canonical non-zero UUID."
    }
    $audience = $ApplicationId
    return [pscustomobject][ordered]@{
        BaseUrl = $canonicalBaseUrl
        PublishEndpoint = "$canonicalBaseUrl/v1/artifacts/publish"
        ApplicationId = $ApplicationId
        TenantId = $TenantId
        TokenAudience = $audience
        TokenScope = "$audience/.default"
    }
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
        $version -match "[/\\?#%]" -or
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

function Invoke-FoundryProjectRequest {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("Get", "Patch", "Delete")]
        [string]$Method,
        [Parameter(Mandatory = $true)]
        [string]$Endpoint,
        [Parameter(Mandatory = $true)]
        [string]$RelativeUri,
        [Parameter(Mandatory = $true)]
        [string]$AccessToken,
        [Parameter(Mandatory = $true)]
        [int]$TimeoutSec,
        [AllowNull()]
        [object]$Body = $null
    )

    $requestEndpoint = Assert-CanonicalFoundryProjectEndpoint `
        -Value $Endpoint `
        -AccountName $AccountName `
        -ProjectName $ProjectName
    try {
        $requestArguments = @{
            Method = $Method
            Uri = "$requestEndpoint$RelativeUri"
            Headers = @{ Authorization = "Bearer $AccessToken" }
            TimeoutSec = $TimeoutSec
            MaximumRedirection = 0
            NoProxy = $true
            SkipHttpErrorCheck = $true
            ErrorAction = "Stop"
        }
        if ($null -ne $Body) {
            $requestArguments.ContentType = "application/json"
            $requestArguments.Body = (
                $Body | ConvertTo-Json -Depth 30 -Compress
            )
        }
        $response = Invoke-WebRequest `
            @requestArguments
    }
    catch {
        throw (
            "Foundry request failed without following redirects: " +
            $_.Exception.Message
        )
    }
    $statusCode = [int]$response.StatusCode
    if ($statusCode -lt 200 -or $statusCode -gt 299) {
        $errorBody = ([string]$response.Content).Trim()
        if ($errorBody.Length -gt 1000) {
            $errorBody = $errorBody.Substring(0, 1000)
        }
        throw (
            "Foundry request '$Method $RelativeUri' failed with HTTP status " +
            "$statusCode. Response: $errorBody"
        )
    }
    if ([string]::IsNullOrWhiteSpace($response.Content)) {
        return $null
    }
    return $response.Content | ConvertFrom-Json
}

if ($Runs -lt 1) {
    throw "Runs must be at least one."
}
if ($ProbeOnly -and $Rollback) {
    throw "ProbeOnly and Rollback are mutually exclusive."
}
if ($ProbeOnly -and $SkipProbe) {
    throw "ProbeOnly cannot be combined with SkipProbe."
}
if ($Rollback -and [string]::IsNullOrWhiteSpace($RollbackRecord)) {
    throw "Rollback requires an explicit run-specific RollbackRecord."
}
$ProjectEndpoint = Resolve-CanonicalFoundryProjectEndpoint `
    -AccountName $AccountName `
    -ProjectName $ProjectName `
    -SuppliedEndpoint $ProjectEndpoint
$script:ConsumerFleetPreflightComplete = $false
$runId = [guid]::NewGuid().ToString("D")
$runNonce = [Convert]::ToHexString(
    [System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32)
).ToLowerInvariant()
$runStartedAt = (Get-Date).ToUniversalTime()

$catalogPath = Join-Path $root "src\agent_iq\data\consumer\agents.json"
$runtimePath = Join-Path $root "src\agent_iq\data\consumer\runtime.json"
$smokePath = Join-Path $root "src\agent_iq\data\consumer\live_smoke.json"
$catalog = (Get-Content $catalogPath -Raw | ConvertFrom-Json).agents
$runtime = (Get-Content $runtimePath -Raw | ConvertFrom-Json).agents
$smoke = (Get-Content $smokePath -Raw | ConvertFrom-Json).agents
$knownIds = @($catalog | ForEach-Object { $_.agentId })
$onlyWasExplicit = $Only.Count -gt 0
if ($Only.Count -eq 0) {
    $Only = $knownIds
}
$Only = @($Only | ForEach-Object {
    $_ -split ","
} | ForEach-Object {
    $_.Trim()
} | Where-Object {
    $_
} | Sort-Object -Unique)

foreach ($agentId in $Only) {
    if ($agentId -notin $knownIds) {
        throw "Unknown canonical consumer agent ID: $agentId"
    }
    if (@($runtime | Where-Object agentId -EQ $agentId).Count -ne 1) {
        throw "Canonical runtime definition is missing or duplicated for $agentId."
    }
    if (@($smoke | Where-Object agentId -EQ $agentId).Count -ne 1) {
        throw "Canonical live smoke contract is missing or duplicated for $agentId."
    }
}
$artifactAgentIds = @(
    $runtime |
        Where-Object {
            $_.agentId -in $Only -and
            $null -ne $_.artifactOutputKinds -and
            @($_.artifactOutputKinds).Count -gt 0
        } |
        ForEach-Object { $_.agentId }
)
$artifactChannel = $null
if ($artifactAgentIds.Count -gt 0 -and -not $Rollback) {
    if ([string]::IsNullOrWhiteSpace($CompanionApplicationId)) {
        throw (
            "Artifact-producing agents require CompanionApplicationId for the " +
            "trusted orchestrator canary companion."
        )
    }
    if ([string]::IsNullOrWhiteSpace($CompanionTenantId)) {
        throw (
            "Artifact-producing agents require CompanionTenantId for the trusted " +
            "orchestrator canary companion."
        )
    }
    $artifactChannel = Resolve-TrustedArtifactChannel `
        -BaseUrl $OrchestratorBaseUrl `
        -AppName $OrchestratorAppName `
        -ApplicationId $CompanionApplicationId `
        -TenantId $CompanionTenantId
}

function Confirm-LiveArtifactCompanionBinding {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Channel
    )

    $companion = & az containerapp show `
        --resource-group $ResourceGroup `
        --name $OrchestratorAppName `
        --output json `
        --only-show-errors | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $null -eq $companion) {
        throw "Trusted Agent IQ artifact companion Container App is unavailable."
    }
    if (
        $companion.name -cne $OrchestratorAppName -or
        $companion.properties.configuration.ingress.external -ne $true -or
        $companion.properties.configuration.ingress.fqdn -cne (
            ([uri]$Channel.BaseUrl).DnsSafeHost
        )
    ) {
        throw "Artifact companion Azure app state does not match the trusted canary host."
    }

    $companionEnvironment = @(
        $companion.properties.template.containers[0].env
    )
    $configuredAudience = $companionEnvironment |
        Where-Object { $_.name -ceq "AGENT_IQ_CANARY_AUDIENCE" } |
        Select-Object -First 1 -ExpandProperty value
    $configuredApplicationId = $companionEnvironment |
        Where-Object {
            $_.name -ceq "AGENT_IQ_ARTIFACT_COMPANION_APPLICATION_ID"
        } |
        Select-Object -First 1 -ExpandProperty value
    $configuredTenantId = $companionEnvironment |
        Where-Object {
            $_.name -ceq "AGENT_IQ_ARTIFACT_COMPANION_TENANT_ID"
        } |
        Select-Object -First 1 -ExpandProperty value

    $auth = & az containerapp auth show `
        --resource-group $ResourceGroup `
        --name $OrchestratorAppName `
        --output json `
        --only-show-errors | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) {
        throw "Artifact companion authentication state is unavailable."
    }
    $easyAuthClientId = $auth.identityProviders.azureActiveDirectory.registration.clientId
    $easyAuthAudiences = @(
        $auth.identityProviders.azureActiveDirectory.validation.allowedAudiences
    )
    $appValidatedIdentity = (
        $configuredApplicationId -ceq $Channel.ApplicationId -and
        $configuredTenantId -ceq $Channel.TenantId -and
        $configuredAudience -ceq $Channel.TokenAudience
    )
    $easyAuthValidatedIdentity = (
        $auth.platform.enabled -eq $true -and
        $easyAuthClientId -ceq $Channel.ApplicationId -and
        $Channel.TokenAudience -cin $easyAuthAudiences
    )
    if (-not $appValidatedIdentity -and -not $easyAuthValidatedIdentity) {
        throw (
            "Artifact companion Azure auth state is not bound to the configured " +
            "application ID and audience."
        )
    }

    $probeBody = @{
        artifact = @{
            schemaVersion = "1.0"
            kind = "demand_scenario"
            artifactId = "consumer-fleet-route-probe"
            productId = "brightwash-cold"
            market = "Germany"
            horizonStart = "2026-10-01"
            horizonEnd = "2026-10-31"
            unit = "cases"
            baselineQuantity = 1000
            forecastQuantity = 1250
            confidence = 0.84
            assumptions = @()
            evidenceIds = @("consumer-fleet-evidence")
            createdAt = "2026-07-13T00:00:00Z"
        }
    } | ConvertTo-Json -Depth 8 -Compress

    try {
        $response = Invoke-WebRequest `
            -Method Post `
            -Uri $Channel.PublishEndpoint `
            -ContentType "application/json" `
            -Body $probeBody `
            -TimeoutSec 30 `
            -MaximumRedirection 0 `
            -NoProxy `
            -SkipHttpErrorCheck `
            -ErrorAction Stop
    }
    catch {
        throw (
            "Artifact companion route probe failed without following redirects: " +
            $_.Exception.Message
        )
    }
    $statusCode = [int]$response.StatusCode
    if ($statusCode -ne 401) {
        throw (
            "Artifact companion route did not reject missing credentials with HTTP 401 " +
            "(received HTTP $statusCode)."
        )
    }

    $response = Invoke-WebRequest `
        -Method Post `
        -Uri $Channel.PublishEndpoint `
        -Headers @{ Authorization = "Bearer invalid-probe-token" } `
        -ContentType "application/json" `
        -Body $probeBody `
        -TimeoutSec 30 `
        -MaximumRedirection 0 `
        -NoProxy `
        -SkipHttpErrorCheck `
        -ErrorAction Stop
    $statusCode = [int]$response.StatusCode
    if ($statusCode -ne 403) {
        throw (
            "Artifact companion route did not reject a bearer token without a ticket " +
            "with HTTP 403 (received HTTP $statusCode)."
        )
    }
}

function Assert-ConsumerFleetMutationAllowed {
    if ($ProbeOnly) {
        throw "ProbeOnly forbids every consumer-fleet mutation."
    }
    if (-not $script:ConsumerFleetPreflightComplete) {
        throw "Consumer-fleet mutation was attempted before preflight completed."
    }
}

function Get-Sha256Text {
    param([Parameter(Mandatory = $true)][string]$Value)

    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
    $hash = [System.Security.Cryptography.SHA256]::HashData($bytes)
    return "sha256:$([Convert]::ToHexString($hash).ToLowerInvariant())"
}

function Add-PayloadIntegrity {
    param([Parameter(Mandatory = $true)][System.Collections.IDictionary]$Payload)

    $canonical = $Payload | ConvertTo-Json -Depth 50 -Compress
    $Payload["integrity"] = [ordered]@{
        algorithm = "sha256"
        payloadSha256 = Get-Sha256Text -Value $canonical
    }
    return $Payload
}

function Write-AtomicJson {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Payload
    )

    $directory = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
    $temporary = Join-Path $directory ".$([IO.Path]::GetFileName($Path)).$runId.tmp"
    $json = ($Payload | ConvertTo-Json -Depth 50) + [Environment]::NewLine
    try {
        [IO.File]::WriteAllText(
            $temporary,
            $json,
            [System.Text.UTF8Encoding]::new($false)
        )
        Move-Item -Force -Path $temporary -Destination $Path
    }
    finally {
        Remove-Item -Force -Path $temporary -ErrorAction SilentlyContinue
    }
}

if (-not $ReportRoot) {
    $ReportRoot = Join-Path $root "reports\deployments\foundry"
}

function Read-ConsumerOperationRecord {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Path
    )

    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw "Rollback requires an explicit run-specific RollbackRecord."
    }
    $resolved = [IO.Path]::GetFullPath($Path)
    $allowedRoot = [IO.Path]::GetFullPath(
        (Join-Path $ReportRoot "consumer-operations")
    ).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (
        -not $resolved.StartsWith(
            $allowedRoot,
            [StringComparison]::OrdinalIgnoreCase
        ) -or
        [IO.Path]::GetFileName($resolved) -ieq "latest.json" -or
        -not (Test-Path -LiteralPath $resolved -PathType Leaf)
    ) {
        throw "RollbackRecord must be an explicit run record under $allowedRoot."
    }
    $rawRecord = Get-Content -LiteralPath $resolved -Raw
    $record = $rawRecord | ConvertFrom-Json
    $expectedHash = [string]$record.integrity.payloadSha256
    if ($expectedHash -cnotmatch "^sha256:[0-9a-f]{64}$") {
        throw "RollbackRecord integrity metadata is missing or malformed."
    }
    $canonical = [System.Text.Json.Nodes.JsonNode]::Parse($rawRecord)
    if ($null -eq $canonical -or $canonical.GetValueKind() -ne "Object") {
        throw "RollbackRecord payload must be a JSON object."
    }
    if (-not $canonical.AsObject().Remove("integrity")) {
        throw "RollbackRecord integrity metadata is missing or malformed."
    }
    $actualHash = Get-Sha256Text -Value (
        $canonical.ToJsonString([System.Text.Json.JsonSerializerOptions]::new())
    )
    if ($actualHash -cne $expectedHash) {
        throw "RollbackRecord payload integrity check failed."
    }
    $recordRunId = [guid]::Empty
    if (
        $record.schemaVersion -cne "1.0" -or
        -not [guid]::TryParseExact(
            [string]$record.runId,
            "D",
            [ref]$recordRunId
        ) -or
        [string]$record.runId -cne $recordRunId.ToString("D") -or
        [IO.Path]::GetFileName($resolved) -cne "$($record.runId).deployment.json" -or
        $record.evidenceType -cne "agent-iq.consumer-fleet-deployment" -or
        $record.projectEndpoint -cne $ProjectEndpoint -or
        $record.accountName -cne $AccountName -or
        $record.projectName -cne $ProjectName -or
        @($record.agents).Count -eq 0
    ) {
        throw "RollbackRecord does not bind the configured Foundry target and deployment run."
    }
    return $record
}

$rollbackData = $null
if ($Rollback) {
    $rollbackData = Read-ConsumerOperationRecord -Path $RollbackRecord
    $recordAgentIds = @(
        $rollbackData.agents |
            ForEach-Object { [string]$_.agentId } |
            Sort-Object -Unique
    )
    if ($recordAgentIds.Count -ne @($rollbackData.agents).Count) {
        throw "RollbackRecord contains missing or duplicate agent IDs."
    }
    foreach ($agentId in $recordAgentIds) {
        if ($agentId -notin $knownIds) {
            throw "RollbackRecord contains unknown agent ID $agentId."
        }
    }
    if ($onlyWasExplicit -and ($Only -join "`n") -cne ($recordAgentIds -join "`n")) {
        throw "Rollback Only selection must exactly match the explicit RollbackRecord."
    }
    $Only = $recordAgentIds
}

. (Join-Path $PSScriptRoot "dev_env.ps1")
. (Join-Path $PSScriptRoot "foundry_login.ps1") -Quiet
$env:PYTHONIOENCODING = "utf-8"

if ($null -ne $artifactChannel -and -not $Rollback) {
    Confirm-LiveArtifactCompanionBinding -Channel $artifactChannel
}

$projectResourceId = (
    "/subscriptions/$env:AZURE_SUBSCRIPTION_ID" +
    "/resourceGroups/$ResourceGroup" +
    "/providers/Microsoft.CognitiveServices/accounts/$AccountName" +
    "/projects/$ProjectName"
)
$searchId = & az search service show `
    --resource-group $SearchResourceGroup `
    --name $SearchService `
    --query id -o tsv --only-show-errors
if ($LASTEXITCODE -ne 0 -or -not $searchId) {
    throw "Azure AI Search service is unavailable."
}
$account = & az cognitiveservices account show `
    --resource-group $ResourceGroup `
    --name $AccountName `
    --only-show-errors -o json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw "Foundry account is unavailable."
}
$accountId = $account.id
$search = & az search service show `
    --resource-group $SearchResourceGroup `
    --name $SearchService `
    --only-show-errors -o json | ConvertFrom-Json
$project = & az rest --method get `
    --url "https://management.azure.com${projectResourceId}?api-version=2025-10-01-preview" |
    ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $null -eq $project) {
    throw "Foundry project lookup failed."
}
$deployerObjectId = & az ad sp show `
    --id $env:AZURE_CLIENT_ID `
    --query id -o tsv
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($deployerObjectId)) {
    throw "Deployer service-principal lookup failed."
}
if (
    [string]::IsNullOrWhiteSpace($accountId) -or
    [string]::IsNullOrWhiteSpace($project.identity.principalId) -or
    [string]::IsNullOrWhiteSpace($search.identity.principalId)
) {
    throw "Foundry or Search managed-identity prerequisites are incomplete."
}

function Ensure-Role {
    param(
        [Parameter(Mandatory = $true)][string]$ObjectId,
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)][string]$Scope
    )

    Assert-ConsumerFleetMutationAllowed
    $assignment = & az role assignment list `
        --assignee-object-id $ObjectId `
        --scope $Scope `
        --query "[?roleDefinitionName=='$Role'].id | [0]" `
        -o tsv
    if ($assignment) {
        return $null
    }
    $createdAssignment = & az role assignment create `
        --assignee-object-id $ObjectId `
        --assignee-principal-type ServicePrincipal `
        --role $Role `
        --scope $Scope `
        --query id `
        --only-show-errors -o tsv
    if (
        $LASTEXITCODE -ne 0 -or
        [string]::IsNullOrWhiteSpace($createdAssignment)
    ) {
        throw "Role assignment failed: $Role for $ObjectId."
    }
    return [string]$createdAssignment
}

$modelState = & az cognitiveservices account deployment show `
    --resource-group $ResourceGroup `
    --name $AccountName `
    --deployment-name $Model `
    --query properties.provisioningState -o tsv --only-show-errors
if ($modelState -ne "Succeeded") {
    throw "Model deployment $Model is not active."
}

function Get-AgentState {
    param([Parameter(Mandatory = $true)][string]$AgentId)

    $requestEndpoint = Assert-CanonicalFoundryProjectEndpoint `
        -Value $ProjectEndpoint `
        -AccountName $AccountName `
        -ProjectName $ProjectName
    $token = & az account get-access-token `
        --scope "https://ai.azure.com/.default" `
        --query accessToken -o tsv
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($token)) {
        throw "Unable to acquire the Foundry data-plane token."
    }
    $listing = Invoke-FoundryProjectRequest `
        -Method Get `
        -Endpoint $requestEndpoint `
        -RelativeUri "/agents?api-version=v1&limit=100" `
        -AccessToken $token `
        -TimeoutSec 60
    $item = $listing.data | Where-Object { $_.id -eq $AgentId }
    if (-not $item) {
        return [pscustomobject]@{
            present = $false
            version = $null
            status = $null
            instanceIdentity = $null
            kind = $null
            deploymentMode = $null
            responsesProtocolVersion = $null
            runtime = $null
            modelDeployment = $null
            toolboxName = $null
            codeContentHash = $null
            image = $null
            imageDigest = $null
        }
    }
    $details = Invoke-FoundryProjectRequest `
        -Method Get `
        -Endpoint $requestEndpoint `
        -RelativeUri "/agents/${AgentId}?api-version=v1" `
        -AccessToken $token `
        -TimeoutSec 60
    $selector = $details.agent_endpoint.version_selector
    if ($null -eq $selector) {
        $selector = $details.agentEndpoint.versionSelector
    }
    $rules = @($selector.version_selection_rules)
    if ($rules.Count -eq 0) {
        $rules = @($selector.versionSelectionRules)
    }
    if ($rules.Count -ne 1) {
        throw "Agent $AgentId traffic must have exactly one immutable version rule."
    }
    $rule = $rules[0]
    $routedVersion = if ($null -ne $rule.agent_version) {
        $rule.agent_version
    }
    else {
        $rule.agentVersion
    }
    $trafficPercentage = if ($null -ne $rule.traffic_percentage) {
        $rule.traffic_percentage
    }
    else {
        $rule.trafficPercentage
    }
    $version = Assert-ExactAgentVersion `
        -Value $routedVersion `
        -Label "Routed agent version"
    if ($rule.type -cne "FixedRatio" -or [int]$trafficPercentage -ne 100) {
        throw "Agent $AgentId traffic must route 100% to one exact version."
    }
    $activeVersion = Invoke-FoundryProjectRequest `
        -Method Get `
        -Endpoint $requestEndpoint `
        -RelativeUri "/agents/$AgentId/versions/${version}?api-version=v1" `
        -AccessToken $token `
        -TimeoutSec 60
    $observedVersion = Assert-ExactAgentVersion `
        -Value $activeVersion.version `
        -Label "Foundry version response"
    if ($observedVersion -cne $version) {
        throw "Foundry returned a different version than the routed agent version."
    }
    $definition = $activeVersion.definition
    $code = $definition.code_configuration
    $container = $definition.container_configuration
    $responses = $definition.protocol_versions |
        Where-Object protocol -EQ "responses" |
        Select-Object -First 1
    $image = $container.image
    $imageDigest = $definition.image_digest
    if (-not $imageDigest -and $image -match "@(?<digest>sha256:[a-fA-F0-9]{64})$") {
        $imageDigest = $Matches.digest.ToLowerInvariant()
    }
    return [pscustomobject]@{
        present = $true
        version = $version
        status = $activeVersion.status
        instanceIdentity = $activeVersion.instance_identity.principal_id
        kind = $definition.kind
        deploymentMode = if ($null -ne $container) { "container" } else { "code" }
        responsesProtocolVersion = $responses.version
        runtime = $code.runtime
        modelDeployment = $definition.environment_variables.AZURE_AI_MODEL_DEPLOYMENT_NAME
        toolboxName = $definition.environment_variables.TOOLBOX_NAME
        codeContentHash = $code.content_hash
        image = $image
        imageDigest = $imageDigest
    }
}

function Get-AgentVersionIds {
    param([Parameter(Mandatory = $true)][string]$AgentId)

    $requestEndpoint = Assert-CanonicalFoundryProjectEndpoint `
        -Value $ProjectEndpoint `
        -AccountName $AccountName `
        -ProjectName $ProjectName
    $token = & az account get-access-token `
        --scope "https://ai.azure.com/.default" `
        --query accessToken -o tsv
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($token)) {
        throw "Unable to acquire the Foundry data-plane token."
    }
    $listing = Invoke-FoundryProjectRequest `
        -Method Get `
        -Endpoint $requestEndpoint `
        -RelativeUri "/agents/$AgentId/versions?api-version=v1&limit=100" `
        -AccessToken $token `
        -TimeoutSec 60
    $items = if ($null -ne $listing.data) {
        @($listing.data)
    }
    elseif ($null -ne $listing.value) {
        @($listing.value)
    }
    else {
        throw "Foundry version listing did not contain a data array."
    }
    return @(
        $items |
            ForEach-Object {
                Assert-ExactAgentVersion `
                    -Value $_.version `
                    -Label "Listed Foundry agent version"
            } |
            Sort-Object -Unique
    )
}

function Set-AgentTrafficVersion {
    param(
        [Parameter(Mandatory = $true)][string]$AgentId,
        [Parameter(Mandatory = $true)][string]$Version
    )

    Assert-ConsumerFleetMutationAllowed
    $exactVersion = Assert-ExactAgentVersion `
        -Value $Version `
        -Label "Traffic target version"
    $requestEndpoint = Assert-CanonicalFoundryProjectEndpoint `
        -Value $ProjectEndpoint `
        -AccountName $AccountName `
        -ProjectName $ProjectName
    $token = & az account get-access-token `
        --scope "https://ai.azure.com/.default" `
        --query accessToken -o tsv
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($token)) {
        throw "Unable to acquire the Foundry data-plane token."
    }
    $body = [ordered]@{
        agent_endpoint = [ordered]@{
            version_selector = [ordered]@{
                version_selection_rules = @(
                    [ordered]@{
                        type = "FixedRatio"
                        agent_version = $exactVersion
                        traffic_percentage = 100
                    }
                )
            }
        }
    }
    $null = Invoke-FoundryProjectRequest `
        -Method Patch `
        -Endpoint $requestEndpoint `
        -RelativeUri "/agents/${AgentId}?api-version=v1" `
        -AccessToken $token `
        -TimeoutSec 120 `
        -Body $body
    $verified = Get-AgentState -AgentId $AgentId
    if (
        -not $verified.present -or
        $verified.version -cne $exactVersion -or
        $verified.status -cne "active"
    ) {
        throw "Foundry traffic did not bind to exact active version $exactVersion."
    }
    return $verified
}

function Remove-NewAgentDeployment {
    param(
        [Parameter(Mandatory = $true)][string]$AgentId,
        [Parameter(Mandatory = $true)][string]$CreatedVersion
    )

    Assert-ConsumerFleetMutationAllowed
    $exactVersion = Assert-ExactAgentVersion `
        -Value $CreatedVersion `
        -Label "Created rollback version"
    $versions = @(Get-AgentVersionIds -AgentId $AgentId)
    if ($versions.Count -ne 1 -or $versions[0] -cne $exactVersion) {
        throw (
            "Refusing to delete $AgentId because its version set no longer matches " +
            "the single version created by this run."
        )
    }
    $requestEndpoint = Assert-CanonicalFoundryProjectEndpoint `
        -Value $ProjectEndpoint `
        -AccountName $AccountName `
        -ProjectName $ProjectName
    $token = & az account get-access-token `
        --scope "https://ai.azure.com/.default" `
        --query accessToken -o tsv
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($token)) {
        throw "Unable to acquire the Foundry data-plane token."
    }
    $result = Invoke-FoundryProjectRequest `
        -Method Delete `
        -Endpoint $requestEndpoint `
        -RelativeUri "/agents/${AgentId}?api-version=v1&force=true" `
        -AccessToken $token `
        -TimeoutSec 120
    if (
        $null -ne $result -and
        $null -ne $result.PSObject.Properties["deleted"] -and
        -not [bool]$result.deleted
    ) {
        throw "New-agent rollback failed for $AgentId."
    }
}

function Initialize-Workspace {
    param([Parameter(Mandatory = $true)][string]$AgentId)

    Assert-ConsumerFleetMutationAllowed
    $parent = Join-Path $WorkRoot $AgentId
    $projectDirectory = Join-Path $parent $AgentId
    if (Test-Path $parent) {
        $recordPath = Join-Path $projectDirectory ".agent-iq-build.json"
        if (-not (Test-Path $recordPath)) {
            throw "Refusing to replace unmarked work directory: $parent"
        }
        $record = Get-Content $recordPath -Raw | ConvertFrom-Json
        if ($record.agentId -ne $AgentId) {
            throw "Work directory marker does not match $AgentId."
        }
        Remove-Item -Recurse -Force $parent
    }
    New-Item -ItemType Directory -Force -Path $parent | Out-Null

    $manifest = Join-Path (Join-Path $ArtifactRoot $AgentId) "azure.yaml"
    Push-Location $parent
    try {
        $null = Assert-CanonicalFoundryProjectEndpoint `
            -Value $ProjectEndpoint `
            -AccountName $AccountName `
            -ProjectName $ProjectName
        & azd ai agent init `
            --manifest $manifest `
            --no-prompt `
            --project-id $projectResourceId `
            --model-deployment $Model `
            --deploy-mode code `
            --runtime python_3_13 `
            --entry-point main.py `
            --agent-name $AgentId `
            --force | Out-Host
        if ($LASTEXITCODE -ne 0) {
            throw "azd initialization failed for $AgentId."
        }
    }
    finally {
        Pop-Location
    }
    if (-not (Test-Path (Join-Path $projectDirectory "azure.yaml"))) {
        throw "Initialized project is missing azure.yaml for $AgentId."
    }
    return $projectDirectory
}

function Ensure-Grounding {
    param(
        [Parameter(Mandatory = $true)][string]$AgentId,
        [Parameter(Mandatory = $true)][string]$ProjectDirectory
    )

    Assert-ConsumerFleetMutationAllowed
    $definition = $runtime | Where-Object { $_.agentId -eq $AgentId }
    if (-not $definition) {
        throw "Missing runtime knowledge binding for $AgentId."
    }
    $knowledgeBase = $definition.knowledgeBindingId
    $searchEndpoint = "https://$SearchService.search.windows.net"
    $knowledgeEndpoint = (
        "$searchEndpoint/knowledgebases/$knowledgeBase/mcp" +
        "?api-version=2026-05-01-preview"
    )
    $connectionName = "$AgentId-kb-mcp"
    $toolboxName = "$AgentId-knowledge"

    Push-Location $ProjectDirectory
    try {
        $null = Assert-CanonicalFoundryProjectEndpoint `
            -Value $ProjectEndpoint `
            -AccountName $AccountName `
            -ProjectName $ProjectName
        & azd env set AGENT_IQ_AZURE_AI_ACCOUNT $AccountName | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Azure AI account configuration failed for $AgentId."
        }
        $null = Assert-CanonicalFoundryProjectEndpoint `
            -Value $ProjectEndpoint `
            -AccountName $AccountName `
            -ProjectName $ProjectName
        & azd env set AGENT_IQ_AZURE_AI_PROJECT $ProjectName | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Azure AI project configuration failed for $AgentId."
        }
        $null = Assert-CanonicalFoundryProjectEndpoint `
            -Value $ProjectEndpoint `
            -AccountName $AccountName `
            -ProjectName $ProjectName
        & azd env set AZURE_AI_MODEL_DEPLOYMENT_NAME $Model | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Model environment configuration failed for $AgentId."
        }
        if (
            $null -ne $definition.artifactOutputKinds -and
            @($definition.artifactOutputKinds).Count -gt 0
        ) {
            if ($null -eq $artifactChannel) {
                throw "Artifact channel binding is unavailable for $AgentId."
            }
            $null = Assert-CanonicalFoundryProjectEndpoint `
                -Value $ProjectEndpoint `
                -AccountName $AccountName `
                -ProjectName $ProjectName
            & azd env set AGENT_IQ_ARTIFACT_PUBLISH_ENDPOINT `
                $artifactChannel.PublishEndpoint | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "Artifact endpoint configuration failed for $AgentId."
            }
            $null = Assert-CanonicalFoundryProjectEndpoint `
                -Value $ProjectEndpoint `
                -AccountName $AccountName `
                -ProjectName $ProjectName
            & azd env set AGENT_IQ_ARTIFACT_COMPANION_APPLICATION_ID `
                $artifactChannel.ApplicationId | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "Artifact companion identity configuration failed for $AgentId."
            }
            & azd env set AGENT_IQ_ARTIFACT_COMPANION_TENANT_ID `
                $artifactChannel.TenantId | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "Artifact companion tenant configuration failed for $AgentId."
            }
        }
        $azdProjectEndpoint = Assert-CanonicalFoundryProjectEndpoint `
            -Value $ProjectEndpoint `
            -AccountName $AccountName `
            -ProjectName $ProjectName
        $previousErrorAction = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $connectionRaw = & azd ai connection show $connectionName `
            --project-endpoint $azdProjectEndpoint `
            --output json 2>$null
        $connectionExists = ($LASTEXITCODE -eq 0 -and $connectionRaw)
        $ErrorActionPreference = $previousErrorAction
        if ($connectionExists) {
            $connection = $connectionRaw | ConvertFrom-Json
            if (
                $connection.target -cne $knowledgeEndpoint -or
                $connection.authType -cne "AgenticIdentityToken"
            ) {
                throw "Existing grounding connection $connectionName does not match $AgentId."
            }
        }
        else {
            $azdProjectEndpoint = Assert-CanonicalFoundryProjectEndpoint `
                -Value $ProjectEndpoint `
                -AccountName $AccountName `
                -ProjectName $ProjectName
            & azd ai connection create $connectionName `
                --kind remote-tool `
                --target $knowledgeEndpoint `
                --auth-type agentic-identity `
                --audience https://search.azure.com `
                --metadata "ApiType=Azure" `
                --project-endpoint $azdProjectEndpoint `
                --no-prompt | Out-Host
            if ($LASTEXITCODE -ne 0) {
                throw "Grounding connection creation failed for $AgentId."
            }
        }

        $previousErrorAction = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $null = Assert-CanonicalFoundryProjectEndpoint `
            -Value $ProjectEndpoint `
            -AccountName $AccountName `
            -ProjectName $ProjectName
        $toolboxRaw = & azd ai toolbox show $toolboxName `
            --project-endpoint $azdProjectEndpoint `
            --output json 2>$null
        $toolboxExists = ($LASTEXITCODE -eq 0)
        $ErrorActionPreference = $previousErrorAction
        if ($toolboxExists) {
            $toolbox = $toolboxRaw | ConvertFrom-Json
            $tool = $toolbox.version.tools | Select-Object -First 1
            if (
                $tool.server_url -ne $knowledgeEndpoint -or
                $tool.project_connection_id -ne $connectionName
            ) {
                throw "Existing toolbox $toolboxName does not match $AgentId."
            }
        }
        else {
            $toolboxFile = Join-Path $ProjectDirectory "toolbox.yaml"
            $resolved = (Get-Content $toolboxFile -Raw).Replace(
                '${KB_MCP_ENDPOINT}',
                $knowledgeEndpoint
            )
            $temporary = Join-Path $env:TEMP "$toolboxName-$PID.yaml"
            Set-Content -Path $temporary -Value $resolved -NoNewline
            try {
                $null = Assert-CanonicalFoundryProjectEndpoint `
                    -Value $ProjectEndpoint `
                    -AccountName $AccountName `
                    -ProjectName $ProjectName
                & azd ai toolbox create $toolboxName `
                    --from-file $temporary `
                    --project-endpoint $azdProjectEndpoint `
                    --no-prompt `
                    --output json | Out-Host
                if ($LASTEXITCODE -ne 0) {
                    throw "Toolbox creation failed for $AgentId."
                }
            }
            finally {
                Remove-Item $temporary -Force -ErrorAction SilentlyContinue
            }
        }
    }
    finally {
        Pop-Location
    }
}

function Invoke-AgentProbe {
    param(
        [Parameter(Mandatory = $true)][string]$AgentId,
        [Parameter(Mandatory = $true)][string]$ProjectDirectory,
        [Parameter(Mandatory = $true)]$ActiveState
    )

    $contract = $smoke | Where-Object { $_.agentId -eq $AgentId }
    if (-not $contract) {
        throw "Missing live smoke contract for $AgentId."
    }
    $destination = Join-Path $ReportRoot $AgentId
    New-Item -ItemType Directory -Force -Path $destination | Out-Null
    $outputs = @()
    for ($run = 1; $run -le $Runs; $run++) {
        $output = Join-Path $destination "$runId-probe-$run.json"
        $probeProjectEndpoint = Assert-CanonicalFoundryProjectEndpoint `
            -Value $ProjectEndpoint `
            -AccountName $AccountName `
            -ProjectName $ProjectName
        $probeArguments = @(
            "run",
            "python",
            "deployments\foundry\consumer\probe.py",
            "--project-endpoint",
            $probeProjectEndpoint,
            "--account-name",
            $AccountName,
            "--project-name",
            $ProjectName,
            "--agent-id",
            $AgentId,
            "--agent-version",
            (Assert-ExactAgentVersion `
                -Value $ActiveState.version `
                -Label "Certified agent version"),
            "--canonical-card",
            (Join-Path $ProjectDirectory "agent-card.json"),
            "--build-record",
            (Join-Path $ProjectDirectory ".agent-iq-build.json"),
            "--output",
            $output,
            "--prompt",
            $contract.prompt
        )
        foreach ($term in $contract.requiredTerms) {
            $probeArguments += @("--required-term", $term)
        }
        foreach ($source in $contract.requiredSources) {
            $probeArguments += @("--required-source", $source)
        }
        if ($contract.sourceTitleMode -ne "required") {
            $probeArguments += "--allow-missing-source-title"
        }
        Push-Location $root
        try {
            & uv @probeArguments | Out-Host
            if ($LASTEXITCODE -ne 0) {
                throw "Native A2A probe $run failed for $AgentId."
            }
        }
        finally {
            Pop-Location
        }
        $outputs += $output
    }
    $runResults = @($outputs | ForEach-Object {
        Get-Content $_ -Raw | ConvertFrom-Json
    })
    $buildRecordPath = Join-Path $ProjectDirectory ".agent-iq-build.json"
    if (-not (Test-Path -LiteralPath $buildRecordPath -PathType Leaf)) {
        throw "Build record is unavailable for $AgentId evidence binding."
    }
    $buildRecord = Get-Content $buildRecordPath -Raw | ConvertFrom-Json
    if (
        $buildRecord.agentId -cne $AgentId -or
        [string]::IsNullOrWhiteSpace($buildRecord.buildHash)
    ) {
        throw "Build record does not bind the canonical package for $AgentId."
    }
    $probePass = (
        @($runResults).Count -eq $Runs -and
        @(
            $runResults |
                Where-Object {
                    $_.status -cne "pass" -or
                    $_.agent.version -cne $ActiveState.version -or
                    $_.agent.status -cne $ActiveState.status
                }
        ).Count -eq 0
    )
    $report = [ordered]@{
        schemaVersion = "2.0"
        evidenceType = "agent-iq.consumer-agent-certification"
        status = if ($probePass) { "pass" } else { "fail" }
        runStartedAt = $runStartedAt.ToString("o")
        generatedAt = (Get-Date).ToUniversalTime().ToString("o")
        runId = $runId
        nonce = $runNonce
        projectEndpoint = $ProjectEndpoint
        projectName = $ProjectName
        agentId = $AgentId
        cleanProcessRuns = $Runs
        package = [ordered]@{
            buildHash = $buildRecord.buildHash
            modelDeployment = $buildRecord.model.deployment
            knowledgeBindingId = $buildRecord.knowledgeBindingId
        }
        deployment = [ordered]@{
            version = $ActiveState.version
            status = $ActiveState.status
            instanceIdentity = $ActiveState.instanceIdentity
            kind = $ActiveState.kind
            deploymentMode = $ActiveState.deploymentMode
            responsesProtocolVersion = $ActiveState.responsesProtocolVersion
            runtime = $ActiveState.runtime
            modelDeployment = $ActiveState.modelDeployment
            toolboxName = $ActiveState.toolboxName
            codeContentHash = $ActiveState.codeContentHash
            image = $ActiveState.image
            imageDigest = $ActiveState.imageDigest
        }
        probeRuns = $runResults
    }
    $report = Add-PayloadIntegrity -Payload $report
    $reportPath = Join-Path $destination "$runId.json"
    Write-AtomicJson -Path $reportPath -Payload $report
    if ($report.status -ne "pass") {
        throw "Aggregate native A2A certification failed for $AgentId."
    }
    return [pscustomobject][ordered]@{
        agentId = $AgentId
        reportPath = $reportPath
        reportSha256 = "sha256:$(
            (Get-FileHash -LiteralPath $reportPath -Algorithm SHA256).Hash.ToLowerInvariant()
        )"
    }
}

function Write-FleetRunManifest {
    param([Parameter(Mandatory = $true)][object[]]$Reports)

    $manifest = [ordered]@{
        schemaVersion = "1.0"
        evidenceType = "agent-iq.consumer-fleet-run-manifest"
        status = "pass"
        runId = $runId
        nonce = $runNonce
        startedAt = $runStartedAt.ToString("o")
        completedAt = (Get-Date).ToUniversalTime().ToString("o")
        projectEndpoint = $ProjectEndpoint
        projectName = $ProjectName
        mode = if ($ProbeOnly) { "probe_only" } else { "deploy_and_probe" }
        reports = @(
            $Reports |
                Sort-Object agentId |
                ForEach-Object {
                    [ordered]@{
                        agentId = $_.agentId
                        reportPath = [IO.Path]::GetRelativePath(
                            [IO.Path]::GetFullPath($ReportRoot),
                            [IO.Path]::GetFullPath($_.reportPath)
                        )
                        reportSha256 = $_.reportSha256
                    }
                }
        )
    }
    $manifest = Add-PayloadIntegrity -Payload $manifest
    $manifestPath = Join-Path (
        Join-Path $ReportRoot "consumer-runs"
    ) "$runId.manifest.json"
    Write-AtomicJson -Path $manifestPath -Payload $manifest
    return $manifestPath
}

function Remove-CreatedAgentVersion {
    param(
        [Parameter(Mandatory = $true)][string]$AgentId,
        [Parameter(Mandatory = $true)][string]$CreatedVersion
    )

    Assert-ConsumerFleetMutationAllowed
    $exactVersion = Assert-ExactAgentVersion `
        -Value $CreatedVersion `
        -Label "Created rollback version"
    $requestEndpoint = Assert-CanonicalFoundryProjectEndpoint `
        -Value $ProjectEndpoint `
        -AccountName $AccountName `
        -ProjectName $ProjectName
    $token = & az account get-access-token `
        --scope "https://ai.azure.com/.default" `
        --query accessToken -o tsv
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($token)) {
        throw "Unable to acquire the Foundry data-plane token."
    }
    $deleteResult = Invoke-FoundryProjectRequest `
        -Method Delete `
        -Endpoint $requestEndpoint `
        -RelativeUri (
            "/agents/$AgentId/versions/$exactVersion" +
            "?api-version=v1&force=true"
        ) `
        -AccessToken $token `
        -TimeoutSec 120
    if (
        $null -ne $deleteResult -and
        $null -ne $deleteResult.PSObject.Properties["deleted"] -and
        -not [bool]$deleteResult.deleted
    ) {
        throw "Created-version rollback failed for $AgentId version $exactVersion."
    }
}

function Write-ConsumerOperationRecord {
    param([Parameter(Mandatory = $true)][object[]]$Entries)

    $payload = [ordered]@{
        schemaVersion = "1.0"
        evidenceType = "agent-iq.consumer-fleet-deployment"
        runId = $runId
        projectEndpoint = $ProjectEndpoint
        accountName = $AccountName
        projectName = $ProjectName
        createdAt = (Get-Date).ToUniversalTime().ToString("o")
        agents = @($Entries)
    }
    $payload = Add-PayloadIntegrity -Payload $payload
    $path = Join-Path (
        Join-Path $ReportRoot "consumer-operations"
    ) "$runId.deployment.json"
    Write-AtomicJson -Path $path -Payload $payload
    return $path
}

function Remove-RecordedAgentRole {
    param([Parameter(Mandatory = $true)][object]$Entry)

    Assert-ConsumerFleetMutationAllowed
    $assignmentId = [string]$Entry.agentSearchRoleAssignmentId
    if ([string]::IsNullOrWhiteSpace($assignmentId)) {
        return
    }
    $roleAssignmentPrefix = (
        ([string]$searchId).TrimEnd("/") +
        "/providers/Microsoft.Authorization/roleAssignments/"
    )
    $roleAssignmentId = [guid]::Empty
    $roleAssignmentName = if (
        $assignmentId.StartsWith(
            $roleAssignmentPrefix,
            [StringComparison]::OrdinalIgnoreCase
        )
    ) {
        $assignmentId.Substring($roleAssignmentPrefix.Length)
    }
    else {
        ""
    }
    if (
        -not [guid]::TryParseExact(
            $roleAssignmentName,
            "D",
            [ref]$roleAssignmentId
        )
    ) {
        throw "RollbackRecord contains a malformed role-assignment ID."
    }
    $principalId = [guid]::Empty
    if (
        -not [guid]::TryParseExact(
            [string]$Entry.deployedInstanceIdentity,
            "D",
            [ref]$principalId
        )
    ) {
        throw "RollbackRecord contains a malformed deployed agent identity."
    }
    $principalIdText = $principalId.ToString("D")
    $assignments = @(
        & az role assignment list `
            --assignee-object-id $principalIdText `
            --scope $searchId `
            --only-show-errors `
            --output json |
            ConvertFrom-Json
    )
    if ($LASTEXITCODE -ne 0) {
        throw "Rollback could not verify the role assignment created by this run."
    }
    $assignment = $assignments |
        Where-Object {
            [string]::Equals(
                [string]$_.id,
                $assignmentId,
                [StringComparison]::OrdinalIgnoreCase
            ) -and
            $_.roleDefinitionName -ceq "Search Index Data Reader" -and
            [string]::Equals(
                [string]$_.principalId,
                $principalIdText,
                [StringComparison]::OrdinalIgnoreCase
            )
        } |
        Select-Object -First 1
    if ($null -eq $assignment) {
        throw "Rollback role assignment no longer matches the deployment record."
    }
    & az role assignment delete `
        --ids $assignmentId `
        --only-show-errors
    if ($LASTEXITCODE -ne 0) {
        throw "Rollback could not remove the role assignment created by this run."
    }
}

function Restore-AgentDeployment {
    param([Parameter(Mandatory = $true)][object]$Entry)

    $agentId = [string]$Entry.agentId
    if ($agentId -notin $knownIds) {
        throw "RollbackRecord contains unknown agent ID $agentId."
    }
    $createdVersion = Assert-ExactAgentVersion `
        -Value $Entry.deployedVersion `
        -Label "Rollback deployed version"
    $current = Get-AgentState -AgentId $agentId
    if (-not [bool]$Entry.previousPresent) {
        if (-not $current.present) {
            Remove-RecordedAgentRole -Entry $Entry
            return [pscustomobject]@{
                agentId = $agentId
                status = "already_rolled_back"
            }
        }
        if ($current.version -cne $createdVersion) {
            throw "Refusing rollback because $agentId traffic changed after deployment."
        }
        Remove-NewAgentDeployment `
            -AgentId $agentId `
            -CreatedVersion $createdVersion
        Remove-RecordedAgentRole -Entry $Entry
        return [pscustomobject]@{
            agentId = $agentId
            status = "rolled_back"
            action = "delete_new_agent"
        }
    }

    $previousVersion = Assert-ExactAgentVersion `
        -Value $Entry.previousVersion `
        -Label "Rollback previous version"
    if (
        -not $current.present -or
        $current.version -cnotin @($createdVersion, $previousVersion)
    ) {
        throw "Refusing rollback because $agentId no longer matches the deployment record."
    }
    $currentVersions = @(Get-AgentVersionIds -AgentId $agentId)
    $expectedPreviousVersions = @(
        @($Entry.previousVersions) | Sort-Object -Unique
    )
    if (
        $current.version -cne $createdVersion -and
        ($currentVersions -join "`n") -ceq
            ($expectedPreviousVersions -join "`n")
    ) {
        Remove-RecordedAgentRole -Entry $Entry
        return [pscustomobject]@{
            agentId = $agentId
            status = "already_rolled_back"
            restoredVersion = $previousVersion
        }
    }
    $expectedCurrentVersions = @(
        $expectedPreviousVersions + $createdVersion |
            Sort-Object -Unique
    )
    if (
        ($currentVersions -join "`n") -cne
            ($expectedCurrentVersions -join "`n")
    ) {
        throw "Refusing rollback because $agentId version inventory changed after deployment."
    }
    if ($current.version -cne $previousVersion) {
        $null = Set-AgentTrafficVersion `
            -AgentId $agentId `
            -Version $previousVersion
    }
    Remove-CreatedAgentVersion `
        -AgentId $agentId `
        -CreatedVersion $createdVersion
    Remove-RecordedAgentRole -Entry $Entry
    $remainingVersions = @(Get-AgentVersionIds -AgentId $agentId)
    if (
        ($remainingVersions -join "`n") -cne
            ($expectedPreviousVersions -join "`n")
    ) {
        throw "Rollback did not restore the exact previous version inventory for $agentId."
    }
    return [pscustomobject]@{
        agentId = $agentId
        status = "rolled_back"
        action = "restore_traffic"
        restoredVersion = $previousVersion
        removedVersion = $createdVersion
    }
}

if ($Rollback) {
    $script:ConsumerFleetPreflightComplete = [bool]$Rollback
    $rollbackResults = @(
        $rollbackData.agents |
            Sort-Object agentId -Descending |
            ForEach-Object { Restore-AgentDeployment -Entry $_ }
    )
    $rollbackResults | Format-Table -AutoSize
    Write-Host "Consumer fleet traffic rollback complete." -ForegroundColor Green
    return
}

$initialStates = @{}
$initialVersionSets = @{}
foreach ($agentId in $Only) {
    $initialStates[$agentId] = Get-AgentState -AgentId $agentId
    $initialVersionSets[$agentId] = if ($initialStates[$agentId].present) {
        @(Get-AgentVersionIds -AgentId $agentId)
    }
    else {
        @()
    }
}

if ($ProbeOnly) {
    $probeDirectories = @{}
    foreach ($agentId in $Only) {
        $state = $initialStates[$agentId]
        if (-not $state.present -or $state.status -cne "active") {
            throw "ProbeOnly requires active live state for $agentId."
        }
        $artifactDirectory = Join-Path $ArtifactRoot $agentId
        foreach ($requiredFile in @(".agent-iq-build.json", "agent-card.json")) {
            if (
                -not (
                    Test-Path -LiteralPath (
                        Join-Path $artifactDirectory $requiredFile
                    ) -PathType Leaf
                )
            ) {
                throw "ProbeOnly artifact prerequisite is missing for $agentId`: $requiredFile"
            }
        }
        $record = Get-Content (
            Join-Path $artifactDirectory ".agent-iq-build.json"
        ) -Raw | ConvertFrom-Json
        if ($record.agentId -cne $agentId -or -not $record.buildHash) {
            throw "ProbeOnly build record is not canonical for $agentId."
        }
        $probeDirectories[$agentId] = $artifactDirectory
    }

    $probeReports = @()
    foreach ($agentId in $Only) {
        Write-Host "`n=== $agentId (probe only) ===" -ForegroundColor Cyan
        $probeReports += Invoke-AgentProbe `
            -AgentId $agentId `
            -ProjectDirectory $probeDirectories[$agentId] `
            -ActiveState $initialStates[$agentId]
    }
    $manifestPath = Write-FleetRunManifest -Reports $probeReports
    Write-Host (
        "`nAgent IQ Foundry fleet probe complete. runId={0} manifest={1}" -f
        $runId,
        $manifestPath
    ) -ForegroundColor Green
    return
}

$script:ConsumerFleetPreflightComplete = $true
Assert-ConsumerFleetMutationAllowed
Push-Location $root
try {
    $generationArguments = @{
        Only = [string[]]$Only
        Out = $ArtifactRoot
    }
    & (Join-Path $PSScriptRoot "generate_consumer_deployments.ps1") @generationArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Canonical deployment generation failed."
    }
}
finally {
    Pop-Location
}

$projectDirectories = @{}
foreach ($agentId in $Only) {
    $projectDirectories[$agentId] = Initialize-Workspace -AgentId $agentId
}

$null = Ensure-Role `
    -ObjectId $deployerObjectId `
    -Role "Foundry Project Manager" `
    -Scope $accountId
$null = Ensure-Role `
    -ObjectId $deployerObjectId `
    -Role "Search Service Contributor" `
    -Scope $searchId
$null = Ensure-Role `
    -ObjectId $deployerObjectId `
    -Role "Search Index Data Contributor" `
    -Scope $searchId
$null = Ensure-Role `
    -ObjectId $project.identity.principalId `
    -Role "Search Index Data Reader" `
    -Scope $searchId
$null = Ensure-Role `
    -ObjectId $search.identity.principalId `
    -Role "Cognitive Services User" `
    -Scope $accountId

$selectedKnowledgeBindings = @(
    $runtime |
        Where-Object { $_.agentId -in $Only } |
        ForEach-Object { [string]$_.knowledgeBindingId } |
        Sort-Object -Unique
)
$knowledgeArguments = @(
    "run",
    "python",
    "-m",
    "agent_iq.deployments.consumer_knowledge",
    "--search-endpoint",
    "https://$SearchService.search.windows.net",
    "--model-endpoint",
    "https://$AccountName.cognitiveservices.azure.com/",
    "--model-deployment",
    $Model
)
foreach ($bindingId in $selectedKnowledgeBindings) {
    $knowledgeArguments += @("--binding", $bindingId)
}
Push-Location $root
try {
    & uv @knowledgeArguments | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Canonical consumer Foundry IQ provisioning failed."
    }
}
finally {
    Pop-Location
}

$results = @()
$probeReports = @()
$deploymentEntries = @()
$operationRecordPath = $null
foreach ($agentId in $Only) {
    Write-Host "`n=== $agentId ===" -ForegroundColor Cyan
    $previousState = $initialStates[$agentId]
    $projectDirectory = $projectDirectories[$agentId]
    $entry = $null

    try {
        Ensure-Grounding `
            -AgentId $agentId `
            -ProjectDirectory $projectDirectory
        Push-Location $projectDirectory
        try {
            $log = Join-Path (Split-Path -Parent $projectDirectory) "azd-up.log"
            $null = Assert-CanonicalFoundryProjectEndpoint `
                -Value $ProjectEndpoint `
                -AccountName $AccountName `
                -ProjectName $ProjectName
            Assert-ConsumerFleetMutationAllowed
            & azd up --no-prompt 2>&1 | Tee-Object -FilePath $log
            if ($LASTEXITCODE -ne 0) {
                throw "Hosted-agent deployment failed for $agentId. See $log"
            }
        }
        finally {
            Pop-Location
        }

        $afterVersions = @(Get-AgentVersionIds -AgentId $agentId)
        $previousVersions = @($initialVersionSets[$agentId])
        $removedVersions = @(
            $previousVersions | Where-Object { $_ -cnotin $afterVersions }
        )
        $createdVersions = @(
            $afterVersions | Where-Object { $_ -cnotin $previousVersions }
        )
        if ($removedVersions.Count -ne 0 -or $createdVersions.Count -ne 1) {
            throw (
                "Hosted-agent deployment for $agentId did not create exactly one immutable " +
                "version without removing prior versions."
            )
        }
        $deployedVersion = Assert-ExactAgentVersion `
            -Value $createdVersions[0] `
            -Label "Newly deployed agent version"
        $activeState = Set-AgentTrafficVersion `
            -AgentId $agentId `
            -Version $deployedVersion
        $entry = [pscustomobject][ordered]@{
            agentId = $agentId
            previousPresent = [bool]$previousState.present
            previousVersion = $previousState.version
            previousVersions = @($previousVersions)
            deployedVersion = $deployedVersion
            deployedInstanceIdentity = $activeState.instanceIdentity
            agentSearchRoleAssignmentId = $null
        }
        $deploymentEntries += $entry
        $operationRecordPath = Write-ConsumerOperationRecord `
            -Entries $deploymentEntries
        $agentRoleAssignmentId = Ensure-Role `
            -ObjectId $activeState.instanceIdentity `
            -Role "Search Index Data Reader" `
            -Scope $searchId
        $entry.agentSearchRoleAssignmentId = $agentRoleAssignmentId
        $operationRecordPath = Write-ConsumerOperationRecord `
            -Entries $deploymentEntries
        if (-not $SkipProbe) {
            $probeReports += Invoke-AgentProbe `
                -AgentId $agentId `
                -ProjectDirectory $projectDirectory `
                -ActiveState $activeState
        }
    }
    catch {
        $originalError = $_.Exception.Message
        $rollbackErrors = @()
        if ($null -eq $entry) {
            try {
                $recoveryPreviousVersions = @($initialVersionSets[$agentId])
                $recoveryCurrentVersions = @(Get-AgentVersionIds -AgentId $agentId)
                $recoveryCreatedVersions = @(
                    $recoveryCurrentVersions |
                        Where-Object { $_ -cnotin $recoveryPreviousVersions }
                )
                $recoveryRemovedVersions = @(
                    $recoveryPreviousVersions |
                        Where-Object { $_ -cnotin $recoveryCurrentVersions }
                )
                if (
                    $recoveryCreatedVersions.Count -eq 1 -and
                    $recoveryRemovedVersions.Count -eq 0
                ) {
                    $recoveryState = Get-AgentState -AgentId $agentId
                    $entry = [pscustomobject][ordered]@{
                        agentId = $agentId
                        previousPresent = [bool]$previousState.present
                        previousVersion = $previousState.version
                        previousVersions = @($recoveryPreviousVersions)
                        deployedVersion = $recoveryCreatedVersions[0]
                        deployedInstanceIdentity = $recoveryState.instanceIdentity
                        agentSearchRoleAssignmentId = $null
                    }
                    $deploymentEntries += $entry
                    $operationRecordPath = Write-ConsumerOperationRecord `
                        -Entries $deploymentEntries
                }
            }
            catch {
                $rollbackErrors += (
                    "$agentId recovery discovery: $($_.Exception.Message)"
                )
            }
        }
        foreach ($rollbackEntry in @($deploymentEntries | Sort-Object agentId -Descending)) {
            try {
                $null = Restore-AgentDeployment -Entry $rollbackEntry
            }
            catch {
                $rollbackErrors += (
                    "$($rollbackEntry.agentId): $($_.Exception.Message)"
                )
            }
        }
        if ($rollbackErrors.Count -gt 0) {
            throw (
                "Deployment/certification failed for $agentId. Original error: " +
                "$originalError Exact rollback also failed: " +
                ($rollbackErrors -join " ")
            )
        }
        throw (
            "Deployment/certification failed for $agentId and exact traffic rollback " +
            "succeeded. Original error: $originalError"
        )
    }
    $results += [pscustomobject]@{
        agentId = $agentId
        status = "active"
        previousVersion = $previousState.version
        version = $deployedVersion
        certified = -not $SkipProbe
    }
}

$manifestPath = $null
if (-not $SkipProbe -and $probeReports.Count -gt 0) {
    $manifestPath = Write-FleetRunManifest -Reports $probeReports
}
Write-Host (
    "`nAgent IQ Foundry fleet operation complete. runId={0} manifest={1}" -f
    $runId,
    $(if ($manifestPath) { $manifestPath } else { "none" })
) -ForegroundColor Green
Write-Host "Rollback record: $operationRecordPath" -ForegroundColor Green
$results | Format-Table -AutoSize
