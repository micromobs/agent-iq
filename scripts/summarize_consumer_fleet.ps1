[CmdletBinding()]
param(
    [string]$ProjectEndpoint = $env:AGENT_IQ_FOUNDRY_PROJECT_ENDPOINT,
    [string]$ReportRoot = "",
    [string[]]$ReportPath = @(),
    [string]$RunManifestPath = "",
    [ValidateRange(1, 1440)]
    [int]$MaxEvidenceAgeMinutes = 60
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

function Assert-CanonicalFoundryProjectEndpoint {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    $canonical = ConvertTo-CanonicalFoundryProjectEndpoint -Value $Value
    if ($Value -cne $canonical) {
        throw "Foundry project endpoint was not canonical at the request boundary."
    }
    return $canonical
}

function Invoke-FoundryProjectRequest {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Endpoint,
        [Parameter(Mandatory = $true)]
        [string]$AccessToken
    )

    $requestEndpoint = Assert-CanonicalFoundryProjectEndpoint -Value $Endpoint
    try {
        $response = Invoke-WebRequest `
            -Method Get `
            -Uri "$requestEndpoint/agents?api-version=v1&limit=100" `
            -Headers @{ Authorization = "******" } `
            -TimeoutSec 60 `
            -MaximumRedirection 0 `
            -NoProxy `
            -SkipHttpErrorCheck `
            -ErrorAction Stop
    }
    catch {
        throw (
            "Foundry request failed without following redirects: " +
            $_.Exception.Message
        )
    }
    $statusCode = [int]$response.StatusCode
    if ($statusCode -lt 200 -or $statusCode -gt 299) {
        throw "Foundry request failed with HTTP status $statusCode."
    }
    return $response.Content | ConvertFrom-Json
}

function Assert-FoundryPathSegment {
    param(
        [Parameter(Mandatory = $true)][string]$Value,
        [Parameter(Mandatory = $true)][string]$Description
    )

    if (
        [string]::IsNullOrWhiteSpace($Value) -or
        $Value -cne $Value.Trim() -or
        $Value.Length -gt 128 -or
        $Value -cnotmatch "^[A-Za-z0-9][A-Za-z0-9._:-]*$"
    ) {
        throw "$Description is not a safe Foundry path segment."
    }
    return $Value
}

function Invoke-FoundryAgentRequest {
    param(
        [Parameter(Mandatory = $true)][string]$Endpoint,
        [Parameter(Mandatory = $true)][string]$AccessToken,
        [Parameter(Mandatory = $true)][string]$AgentId,
        [AllowNull()][string]$AgentVersion
    )

    $requestEndpoint = Assert-CanonicalFoundryProjectEndpoint -Value $Endpoint
    $validatedAgentId = Assert-FoundryPathSegment `
        -Value $AgentId `
        -Description "Agent ID"
    $relativePath = "/agents/$([uri]::EscapeDataString($validatedAgentId))"
    if ($null -ne $AgentVersion) {
        $validatedVersion = Assert-FoundryPathSegment `
            -Value $AgentVersion `
            -Description "Agent version"
        $relativePath += "/versions/$([uri]::EscapeDataString($validatedVersion))"
    }
    try {
        $response = Invoke-WebRequest `
            -Method Get `
            -Uri "$requestEndpoint${relativePath}?api-version=v1" `
            -Headers @{ Authorization = "******" } `
            -TimeoutSec 60 `
            -MaximumRedirection 0 `
            -NoProxy `
            -SkipHttpErrorCheck `
            -ErrorAction Stop
    }
    catch {
        throw (
            "Foundry agent request failed without following redirects: " +
            $_.Exception.Message
        )
    }
    $statusCode = [int]$response.StatusCode
    if ($statusCode -lt 200 -or $statusCode -gt 299) {
        throw "Foundry agent request failed with HTTP status $statusCode."
    }
    return $response.Content | ConvertFrom-Json
}

function Get-ObjectPropertyValue {
    param(
        [Parameter(Mandatory = $true)]$Object,
        [Parameter(Mandatory = $true)][string[]]$Names
    )

    foreach ($name in $Names) {
        $property = $Object.PSObject.Properties[$name]
        if ($null -ne $property) {
            return $property.Value
        }
    }
    return $null
}

function Get-FixedRoutedVersion {
    param(
        [Parameter(Mandatory = $true)]$Payload,
        [Parameter(Mandatory = $true)][string]$AgentId
    )

    $endpoint = Get-ObjectPropertyValue `
        -Object $Payload `
        -Names @("agent_endpoint", "agentEndpoint")
    if ($null -eq $endpoint) {
        throw "$AgentId has no live Foundry endpoint metadata."
    }
    $selector = Get-ObjectPropertyValue `
        -Object $endpoint `
        -Names @("version_selector", "versionSelector")
    $rules = if ($null -ne $selector) {
        Get-ObjectPropertyValue `
            -Object $selector `
            -Names @("version_selection_rules", "versionSelectionRules")
    }
    else {
        $null
    }
    $ruleList = @($rules)
    if ($ruleList.Count -ne 1) {
        throw "$AgentId must have exactly one live Foundry traffic rule."
    }
    $rule = $ruleList[0]
    $version = Get-ObjectPropertyValue `
        -Object $rule `
        -Names @("agent_version", "agentVersion")
    $percentage = Get-ObjectPropertyValue `
        -Object $rule `
        -Names @("traffic_percentage", "trafficPercentage")
    $percentageType = if ($null -eq $percentage) {
        [TypeCode]::Empty
    }
    else {
        [Type]::GetTypeCode($percentage.GetType())
    }
    $integerTypes = @(
        [TypeCode]::SByte,
        [TypeCode]::Byte,
        [TypeCode]::Int16,
        [TypeCode]::UInt16,
        [TypeCode]::Int32,
        [TypeCode]::UInt32,
        [TypeCode]::Int64,
        [TypeCode]::UInt64
    )
    if (
        $rule.type -cne "FixedRatio" -or
        $percentageType -notin $integerTypes -or
        [int64]$percentage -ne 100
    ) {
        throw "$AgentId traffic is not fixed 100% to one immutable version."
    }
    return Assert-FoundryPathSegment `
        -Value ([string]$version) `
        -Description "$AgentId routed version"
}

function Get-Sha256Text {
    param([Parameter(Mandatory = $true)][string]$Value)

    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
    $hash = [System.Security.Cryptography.SHA256]::HashData($bytes)
    return "sha256:$([Convert]::ToHexString($hash).ToLowerInvariant())"
}

function Assert-PayloadIntegrity {
    param(
        [Parameter(Mandatory = $true)]$Payload,
        [Parameter(Mandatory = $true)][string]$Description
    )

    $integrity = $Payload.integrity
    if (
        $null -eq $integrity -or
        $integrity.algorithm -cne "sha256" -or
        $integrity.payloadSha256 -cnotmatch "^sha256:[a-f0-9]{64}$"
    ) {
        throw "$Description has no valid payload integrity binding."
    }
    $expected = $integrity.payloadSha256
    $Payload.PSObject.Properties.Remove("integrity")
    try {
        $canonical = $Payload | ConvertTo-Json -Depth 50 -Compress
        $actual = Get-Sha256Text -Value $canonical
    }
    finally {
        $Payload | Add-Member -NotePropertyName integrity -NotePropertyValue $integrity
    }
    if ($actual -cne $expected) {
        throw "$Description payload integrity check failed."
    }
}

function ConvertTo-EvidenceTimestamp {
    param(
        [Parameter(Mandatory = $true)][string]$Value,
        [Parameter(Mandatory = $true)][string]$Description
    )

    $timestamp = [DateTimeOffset]::MinValue
    if (
        -not [DateTimeOffset]::TryParseExact(
            $Value,
            "o",
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::RoundtripKind,
            [ref]$timestamp
        )
    ) {
        throw "$Description does not contain a canonical round-trip timestamp."
    }
    return $timestamp.ToUniversalTime()
}

function Assert-FreshTimestamp {
    param(
        [Parameter(Mandatory = $true)][DateTimeOffset]$Timestamp,
        [Parameter(Mandatory = $true)][DateTimeOffset]$Now,
        [Parameter(Mandatory = $true)][string]$Description
    )

    if ($Timestamp -gt $Now.AddMinutes(5)) {
        throw "$Description is future-dated."
    }
    if ($Timestamp -lt $Now.AddMinutes(-$MaxEvidenceAgeMinutes)) {
        throw "$Description is stale."
    }
}

function Assert-CanonicalRunIdentity {
    param(
        [Parameter(Mandatory = $true)][string]$RunId,
        [Parameter(Mandatory = $true)][string]$Nonce,
        [Parameter(Mandatory = $true)][string]$Description
    )

    $parsedRunId = [guid]::Empty
    if (
        -not [guid]::TryParseExact($RunId, "D", [ref]$parsedRunId) -or
        $parsedRunId -eq [guid]::Empty -or
        $RunId -cne $parsedRunId.ToString("D")
    ) {
        throw "$Description has a noncanonical run ID."
    }
    if ($Nonce -cnotmatch "^[a-f0-9]{64}$") {
        throw "$Description has a noncanonical run nonce."
    }
}

function Assert-ExpectedEvidenceIdentity {
    param(
        [Parameter(Mandatory = $true)]$Evidence,
        [Parameter(Mandatory = $true)][string]$ExpectedProjectEndpoint,
        [AllowNull()][string]$ExpectedRunId,
        [AllowNull()][string]$ExpectedNonce,
        [AllowNull()][string]$ExpectedAgentId
    )

    Assert-CanonicalRunIdentity `
        -RunId $Evidence.runId `
        -Nonce $Evidence.nonce `
        -Description "Per-agent report $($Evidence.agentId)"
    if ($Evidence.projectEndpoint -cne $ExpectedProjectEndpoint) {
        throw "Per-agent evidence is bound to a different Foundry project."
    }
    if (
        $null -ne $ExpectedRunId -and (
            $Evidence.runId -cne $ExpectedRunId -or
            $Evidence.nonce -cne $ExpectedNonce
        )
    ) {
        throw "Cross-run evidence was supplied."
    }
    if ($null -ne $ExpectedAgentId -and $Evidence.agentId -cne $ExpectedAgentId) {
        throw "Cross-agent evidence was supplied."
    }
}

function Assert-ExactConsumerCoverage {
    param(
        [Parameter(Mandatory = $true)][string[]]$ExpectedAgentIds,
        [Parameter(Mandatory = $true)][string[]]$ActualAgentIds,
        [Parameter(Mandatory = $true)][string]$Description
    )

    $expected = @($ExpectedAgentIds | Sort-Object)
    $actual = @($ActualAgentIds | Sort-Object)
    if (
        $actual.Count -ne $expected.Count -or
        ($actual -join "`n") -cne ($expected -join "`n")
    ) {
        throw "$Description does not cover the exact canonical consumer fleet."
    }
}

function Add-UniqueEvidenceBinding {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [AllowNull()][string]$AgentId,
        [Parameter(Mandatory = $true)]
        [Collections.Generic.HashSet[string]]$SeenPaths,
        [Parameter(Mandatory = $true)]
        [Collections.Generic.HashSet[string]]$SeenAgentIds
    )

    if (-not $SeenPaths.Add($Path)) {
        throw "Duplicate or colliding per-agent report path: $Path"
    }
    if ($null -ne $AgentId -and -not $SeenAgentIds.Add($AgentId)) {
        throw "Duplicate or colliding per-agent identity: $AgentId"
    }
}

function Resolve-AuthorizedEvidencePath {
    param(
        [Parameter(Mandatory = $true)][string]$Candidate,
        [Parameter(Mandatory = $true)][string]$EvidenceRoot,
        [Parameter(Mandatory = $true)][string]$Description
    )

    if (
        [string]::IsNullOrWhiteSpace($Candidate) -or
        $Candidate -match "(?i)(?:^|[\\/])latest\.json(?:$|[\\/])"
    ) {
        throw "$Description must be an explicit run-specific path; latest.json is forbidden."
    }
    $rootPath = [IO.Path]::GetFullPath($EvidenceRoot).TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
    $candidatePath = if ([IO.Path]::IsPathRooted($Candidate)) {
        [IO.Path]::GetFullPath($Candidate)
    }
    else {
        [IO.Path]::GetFullPath((Join-Path $rootPath $Candidate))
    }
    $rootPrefix = $rootPath + [IO.Path]::DirectorySeparatorChar
    if (
        -not $candidatePath.StartsWith(
            $rootPrefix,
            [StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "$Description escapes the configured report root."
    }
    if (-not (Test-Path -LiteralPath $candidatePath -PathType Leaf)) {
        throw "$Description does not exist."
    }
    $item = Get-Item -LiteralPath $candidatePath -Force
    $current = $item
    while (
        $null -ne $current -and
        $current.FullName -ine $rootPath
    ) {
        if (($current.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "$Description must not traverse a reparse-point alias."
        }
        $current = $current.Directory
    }
    if ($null -eq $current) {
        throw "$Description could not be bound to the configured report root."
    }
    return $item.FullName
}

function Write-AtomicJson {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Payload,
        [Parameter(Mandatory = $true)][string]$RunId
    )

    $directory = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
    $temporary = Join-Path $directory ".$([IO.Path]::GetFileName($Path)).$RunId.tmp"
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

function Add-PayloadIntegrity {
    param([Parameter(Mandatory = $true)][System.Collections.IDictionary]$Payload)

    $canonical = $Payload | ConvertTo-Json -Depth 50 -Compress
    $Payload["integrity"] = [ordered]@{
        algorithm = "sha256"
        payloadSha256 = Get-Sha256Text -Value $canonical
    }
    return $Payload
}

function Get-LiveConsumerSnapshot {
    param(
        [Parameter(Mandatory = $true)][string]$Endpoint,
        [Parameter(Mandatory = $true)][string]$AccessToken,
        [Parameter(Mandatory = $true)][string]$AgentId,
        [Parameter(Mandatory = $true)][string]$ExpectedVersion
    )

    $details = Invoke-FoundryAgentRequest `
        -Endpoint $Endpoint `
        -AccessToken $AccessToken `
        -AgentId $AgentId
    $routedVersion = Get-FixedRoutedVersion `
        -Payload $details `
        -AgentId $AgentId
    if ($routedVersion -cne $ExpectedVersion) {
        throw "$AgentId live traffic does not target the certified immutable version."
    }
    $exactVersion = Invoke-FoundryAgentRequest `
        -Endpoint $Endpoint `
        -AccessToken $AccessToken `
        -AgentId $AgentId `
        -AgentVersion $ExpectedVersion
    if ([string]$exactVersion.version -cne $ExpectedVersion) {
        throw "$AgentId exact-version response does not match its requested version."
    }
    $definition = $exactVersion.definition
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
    return [pscustomobject][ordered]@{
        agentId = $AgentId
        version = $exactVersion.version
        status = $exactVersion.status
        instanceIdentity = $exactVersion.instance_identity.principal_id
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

function Assert-ProbeRuns {
    param(
        [Parameter(Mandatory = $true)]$Evidence,
        [Parameter(Mandatory = $true)][DateTimeOffset]$Now,
        [Parameter(Mandatory = $true)][DateTimeOffset]$RunStartedAt,
        [Parameter(Mandatory = $true)][DateTimeOffset]$RunCompletedAt
    )

    $runs = @($Evidence.probeRuns)
    if (
        $Evidence.cleanProcessRuns -lt 1 -or
        $runs.Count -ne $Evidence.cleanProcessRuns
    ) {
        throw "$($Evidence.agentId) has missing or inconsistent probe runs."
    }
    $requiredChecks = @(
        "agentActive",
        "hostedKind",
        "responsesProtocolV2",
        "pythonRuntimeIs313",
        "modelDeploymentMatches",
        "canonicalToolboxBound",
        "codeContentHashPresent",
        "deploymentContentPresent",
        "trafficBoundToCertifiedVersion",
        "cardNameMatches",
        "cardVersionIsV1",
        "cardMatchesCanonical",
        "nativeCardTracePresent",
        "nativeMessageTracePresent",
        "answerNonempty",
        "requiredTermsPresent",
        "sourceTitlePolicySatisfied",
        "sourceReferencePresent",
        "traceContainsNoPrompt",
        "traceContainsNoToken",
        "traceContainsNoAuthorization"
    )
    foreach ($run in $runs) {
        if ($run.status -cne "pass" -or @($run.failures).Count -ne 0) {
            throw "$($Evidence.agentId) contains a failed probe run."
        }
        $checkedAt = ConvertTo-EvidenceTimestamp `
            -Value $run.checkedAt `
            -Description "$($Evidence.agentId) probe checkedAt"
        Assert-FreshTimestamp `
            -Timestamp $checkedAt `
            -Now $Now `
            -Description "$($Evidence.agentId) probe"
        if (
            $checkedAt -lt $RunStartedAt.AddMinutes(-1) -or
            $checkedAt -gt $RunCompletedAt.AddMinutes(1)
        ) {
            throw "$($Evidence.agentId) probe timestamp is outside the authorized run."
        }
        if (
            $run.agent.version -cne $Evidence.deployment.version -or
            $run.agent.status -cne $Evidence.deployment.status -or
            $run.agent.kind -cne $Evidence.deployment.kind -or
            $run.agent.deploymentMode -cne $Evidence.deployment.deploymentMode -or
            $run.agent.responsesProtocolVersion -cne (
                $Evidence.deployment.responsesProtocolVersion
            )
        ) {
            throw "$($Evidence.agentId) probe is bound to a different live deployment."
        }
        foreach ($check in $requiredChecks) {
            if ($run.checks.$check -ne $true) {
                throw "$($Evidence.agentId) probe check $check did not pass."
            }
        }
        if (
            $run.checks.sourceTitlesPresent -eq $false -and
            $run.invocation.sourceTitleMode -cne "advisory"
        ) {
            throw "$($Evidence.agentId) omitted a required source title."
        }
        if (
            $run.card.matchesCanonical -ne $true -or
            $run.card.canonicalHash -cne $run.card.servedHash -or
            $run.trace.payloadsCaptured -ne $false -or
            $run.trace.eventCount -lt 2
        ) {
            throw "$($Evidence.agentId) card or content-safe trace binding is invalid."
        }
    }
}

if (-not $ReportRoot) {
    $ReportRoot = Join-Path $root "reports\deployments\foundry"
}
$hasExplicitReports = @($ReportPath).Count -gt 0
$hasManifest = -not [string]::IsNullOrWhiteSpace($RunManifestPath)
if ($hasExplicitReports -eq $hasManifest) {
    throw "Supply exactly one of -ReportPath or -RunManifestPath."
}

$catalogPath = Join-Path $root "src\agent_iq\data\consumer\agents.json"
$runtimePath = Join-Path $root "src\agent_iq\data\consumer\runtime.json"
$expectedConsumer = @(
    (Get-Content $catalogPath -Raw | ConvertFrom-Json).agents |
        ForEach-Object { $_.agentId } |
        Sort-Object
)
$runtime = @((Get-Content $runtimePath -Raw | ConvertFrom-Json).agents)
$now = [DateTimeOffset]::UtcNow
$manifest = $null
$manifestPath = $null
$manifestReports = @()

if ($hasManifest) {
    $manifestPath = Resolve-AuthorizedEvidencePath `
        -Candidate $RunManifestPath `
        -EvidenceRoot $ReportRoot `
        -Description "Run manifest"
    $ProjectEndpoint = ConvertTo-CanonicalFoundryProjectEndpoint -Value $ProjectEndpoint
    $expectedProjectName = $ProjectEndpoint.Substring($ProjectEndpoint.LastIndexOf("/") + 1)
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    Assert-PayloadIntegrity -Payload $manifest -Description "Run manifest"
    if (
        $manifest.schemaVersion -cne "1.0" -or
        $manifest.evidenceType -cne "agent-iq.consumer-fleet-run-manifest" -or
        $manifest.status -cne "pass" -or
        $manifest.projectEndpoint -cne $ProjectEndpoint -or
        $manifest.projectName -cne $expectedProjectName -or
        $manifest.mode -notin @("probe_only", "deploy_and_probe")
    ) {
        throw "Run manifest contract does not match this fleet certification."
    }
    Assert-CanonicalRunIdentity `
        -RunId $manifest.runId `
        -Nonce $manifest.nonce `
        -Description "Run manifest"
    if (
        [IO.Path]::GetFileName($manifestPath) -cne "$($manifest.runId).manifest.json" -or
        (Split-Path (Split-Path -Parent $manifestPath) -Leaf) -cne "consumer-runs"
    ) {
        throw "Run manifest path is not bound to its run identity."
    }
    $runStartedAt = ConvertTo-EvidenceTimestamp `
        -Value $manifest.startedAt `
        -Description "Run manifest startedAt"
    $runCompletedAt = ConvertTo-EvidenceTimestamp `
        -Value $manifest.completedAt `
        -Description "Run manifest completedAt"
    Assert-FreshTimestamp `
        -Timestamp $runCompletedAt `
        -Now $now `
        -Description "Run manifest"
    if ($runStartedAt -gt $runCompletedAt) {
        throw "Run manifest completion precedes its start."
    }
    $manifestReports = @($manifest.reports)
    if ($manifestReports.Count -ne $expectedConsumer.Count) {
        throw "Run manifest is missing consumer-agent reports."
    }
}
else {
    $runStartedAt = $now.AddMinutes(-$MaxEvidenceAgeMinutes)
    $runCompletedAt = $now
    $manifestReports = @(
        $ReportPath | ForEach-Object {
            [pscustomobject]@{
                agentId = $null
                reportPath = $_
                reportSha256 = $null
            }
        }
    )
}

$resolvedReports = @()
$seenPaths = [Collections.Generic.HashSet[string]]::new(
    [StringComparer]::OrdinalIgnoreCase
)
$seenManifestAgentIds = [Collections.Generic.HashSet[string]]::new(
    [StringComparer]::Ordinal
)
foreach ($entry in $manifestReports) {
    $resolvedPath = Resolve-AuthorizedEvidencePath `
        -Candidate $entry.reportPath `
        -EvidenceRoot $ReportRoot `
        -Description "Per-agent report"
    Add-UniqueEvidenceBinding `
        -Path $resolvedPath `
        -AgentId $(if ($hasManifest) { [string]$entry.agentId } else { $null }) `
        -SeenPaths $seenPaths `
        -SeenAgentIds $seenManifestAgentIds
    if ($hasManifest) {
        if (
            $entry.agentId -notin $expectedConsumer -or
            [string]::IsNullOrWhiteSpace($entry.agentId)
        ) {
            throw "Run manifest contains a missing, duplicate, or unknown agent ID."
        }
        $actualFileHash = "sha256:$(
            (Get-FileHash -LiteralPath $resolvedPath -Algorithm SHA256).Hash.ToLowerInvariant()
        )"
        if (
            $entry.reportSha256 -cnotmatch "^sha256:[a-f0-9]{64}$" -or
            $actualFileHash -cne $entry.reportSha256
        ) {
            throw "Run manifest report hash failed for $($entry.agentId)."
        }
    }
    $resolvedReports += [pscustomobject]@{
        manifestAgentId = $entry.agentId
        path = $resolvedPath
    }
}

if (-not $hasManifest) {
    $ProjectEndpoint = ConvertTo-CanonicalFoundryProjectEndpoint -Value $ProjectEndpoint
    $expectedProjectName = $ProjectEndpoint.Substring($ProjectEndpoint.LastIndexOf("/") + 1)
}

$evidenceByAgent = @{}
$runId = $null
$runNonce = $null
$evidenceRunStartedAt = $null
foreach ($resolved in $resolvedReports) {
    $evidence = Get-Content -LiteralPath $resolved.path -Raw | ConvertFrom-Json
    Assert-PayloadIntegrity `
        -Payload $evidence `
        -Description "Per-agent report $($resolved.path)"
    if (
        $evidence.schemaVersion -cne "2.0" -or
        $evidence.evidenceType -cne "agent-iq.consumer-agent-certification" -or
        $evidence.status -cne "pass" -or
        $evidence.projectEndpoint -cne $ProjectEndpoint -or
        $evidence.projectName -cne $expectedProjectName -or
        $evidence.agentId -notin $expectedConsumer
    ) {
        throw "Per-agent report contract is invalid: $($resolved.path)"
    }
    Assert-ExpectedEvidenceIdentity `
        -Evidence $evidence `
        -ExpectedProjectEndpoint $ProjectEndpoint `
        -ExpectedRunId $(if ($null -eq $runId) { $null } else { $runId }) `
        -ExpectedNonce $(if ($null -eq $runNonce) { $null } else { $runNonce }) `
        -ExpectedAgentId $(
            if ($hasManifest) { [string]$resolved.manifestAgentId } else { $null }
        )
    if ($null -eq $runId) {
        $runId = $evidence.runId
        $runNonce = $evidence.nonce
    }
    if ($hasManifest) {
        Assert-ExpectedEvidenceIdentity `
            -Evidence $evidence `
            -ExpectedProjectEndpoint $ProjectEndpoint `
            -ExpectedRunId $manifest.runId `
            -ExpectedNonce $manifest.nonce `
            -ExpectedAgentId $resolved.manifestAgentId
    }
    if (
        [IO.Path]::GetFileName($resolved.path) -cne "$($evidence.runId).json" -or
        (Split-Path (Split-Path -Parent $resolved.path) -Leaf) -cne $evidence.agentId
    ) {
        throw "Per-agent report path is not bound to its run and agent identity."
    }
    if ($evidenceByAgent.ContainsKey($evidence.agentId)) {
        throw "Duplicate or cross-agent evidence was supplied for $($evidence.agentId)."
    }
    $reportStartedAt = ConvertTo-EvidenceTimestamp `
        -Value $evidence.runStartedAt `
        -Description "$($evidence.agentId) runStartedAt"
    $generatedAt = ConvertTo-EvidenceTimestamp `
        -Value $evidence.generatedAt `
        -Description "$($evidence.agentId) generatedAt"
    Assert-FreshTimestamp `
        -Timestamp $reportStartedAt `
        -Now $now `
        -Description "$($evidence.agentId) run start"
    Assert-FreshTimestamp `
        -Timestamp $generatedAt `
        -Now $now `
        -Description "$($evidence.agentId) report"
    if ($null -eq $evidenceRunStartedAt) {
        $evidenceRunStartedAt = $reportStartedAt
    }
    elseif ($reportStartedAt -ne $evidenceRunStartedAt) {
        throw "Cross-run evidence start timestamps were supplied."
    }
    if (
        $reportStartedAt -gt $generatedAt -or
        ($hasManifest -and $reportStartedAt -ne $runStartedAt)
    ) {
        throw "$($evidence.agentId) report is not bound to the authorized run start."
    }
    if (
        $generatedAt -lt $runStartedAt.AddMinutes(-1) -or
        $generatedAt -gt $runCompletedAt.AddMinutes(1)
    ) {
        throw "$($evidence.agentId) report timestamp is outside the authorized run."
    }
    $expectedRuntime = $runtime |
        Where-Object agentId -EQ $evidence.agentId |
        Select-Object -First 1
    if (
        $evidence.package.buildHash -cnotmatch "^[a-f0-9]{64}$" -or
        $evidence.package.modelDeployment -cne "gpt-5.4-mini" -or
        $evidence.package.knowledgeBindingId -cne $expectedRuntime.knowledgeBindingId
    ) {
        throw "$($evidence.agentId) package binding is missing or invalid."
    }
    Assert-ProbeRuns `
        -Evidence $evidence `
        -Now $now `
        -RunStartedAt $reportStartedAt `
        -RunCompletedAt $generatedAt
    $evidenceByAgent[$evidence.agentId] = [pscustomobject]@{
        evidence = $evidence
        path = $resolved.path
    }
}

Assert-ExactConsumerCoverage `
    -ExpectedAgentIds $expectedConsumer `
    -ActualAgentIds @($evidenceByAgent.Keys) `
    -Description "Explicit evidence"

. (Join-Path $PSScriptRoot "foundry_login.ps1") -Quiet
$requestEndpoint = Assert-CanonicalFoundryProjectEndpoint -Value $ProjectEndpoint
$token = & az account get-access-token `
    --scope "https://ai.azure.com/.default" `
    --query accessToken -o tsv
if ($LASTEXITCODE -ne 0 -or -not $token) {
    throw "Unable to acquire the Foundry data-plane token."
}
$listing = Invoke-FoundryProjectRequest `
    -Endpoint $requestEndpoint `
    -AccessToken $token
if ($listing.has_more) {
    throw "Agent inventory exceeds the certified 100-item page."
}

$liveConsumer = @($listing.data | Where-Object id -Like "consumer-*" | Sort-Object id)
$liveConsumerIds = @($liveConsumer | ForEach-Object id)
if (($liveConsumerIds -join "`n") -cne ($expectedConsumer -join "`n")) {
    throw "Live consumer inventory does not exactly match the canonical 20 IDs."
}

$agentEvidence = @()
foreach ($agentId in $expectedConsumer) {
    $evidence = $evidenceByAgent[$agentId].evidence
    $live = Get-LiveConsumerSnapshot `
        -Endpoint $requestEndpoint `
        -AccessToken $token `
        -AgentId $agentId `
        -ExpectedVersion ([string]$evidence.deployment.version)
    foreach ($property in @(
        "version",
        "status",
        "instanceIdentity",
        "kind",
        "deploymentMode",
        "responsesProtocolVersion",
        "runtime",
        "modelDeployment",
        "toolboxName",
        "codeContentHash",
        "image",
        "imageDigest"
    )) {
        if ($evidence.deployment.$property -cne $live.$property) {
            throw "$agentId evidence does not match live $property."
        }
    }
    if (
        $live.status -cne "active" -or
        $live.kind -cne "hosted" -or
        $live.responsesProtocolVersion -cne "2.0.0" -or
        $live.modelDeployment -cne "gpt-5.4-mini" -or
        $live.toolboxName -cne "$agentId-knowledge" -or
        [string]::IsNullOrWhiteSpace($live.version) -or
        [string]::IsNullOrWhiteSpace($live.instanceIdentity)
    ) {
        throw "$agentId live hosted-agent metadata failed certification."
    }
    if (
        $live.deploymentMode -ceq "code" -and (
            $live.runtime -cne "python_3_13" -or
            [string]::IsNullOrWhiteSpace($live.codeContentHash) -or
            $null -ne $live.image -or
            $null -ne $live.imageDigest
        )
    ) {
        throw "$agentId live code deployment lacks an immutable content binding."
    }
    if (
        $live.deploymentMode -ceq "container" -and (
            [string]::IsNullOrWhiteSpace($live.image) -or
            [string]::IsNullOrWhiteSpace($live.imageDigest) -or
            $live.image -cnotmatch "@sha256:[a-fA-F0-9]{64}$"
        )
    ) {
        throw "$agentId live container deployment lacks an immutable digest binding."
    }
    $lastRun = @($evidence.probeRuns)[-1]
    $agentEvidence += [ordered]@{
        agentId = $agentId
        version = $live.version
        status = $live.status
        deploymentMode = $live.deploymentMode
        codeContentHash = $live.codeContentHash
        image = $live.image
        imageDigest = $live.imageDigest
        packageBuildHash = $evidence.package.buildHash
        responsesProtocolVersion = $live.responsesProtocolVersion
        modelDeployment = $live.modelDeployment
        toolboxName = $live.toolboxName
        cardHash = $lastRun.card.servedHash
        nativeA2AStatus = $lastRun.status
        citationReferencePresent = $lastRun.checks.sourceReferencePresent
        evidencePath = [IO.Path]::GetRelativePath(
            [IO.Path]::GetFullPath($ReportRoot),
            [IO.Path]::GetFullPath($evidenceByAgent[$agentId].path)
        )
    }
}

$payload = [ordered]@{
    schemaVersion = "2.0"
    evidenceType = "agent-iq.consumer-fleet-certificate"
    generatedAt = (Get-Date).ToUniversalTime().ToString("o")
    status = "pass"
    runId = $runId
    nonce = $runNonce
    projectEndpoint = $ProjectEndpoint
    projectName = $expectedProjectName
    authorization = [ordered]@{
        mode = if ($hasManifest) { "run_manifest" } else { "explicit_reports" }
        manifestPath = if ($manifestPath) {
            [IO.Path]::GetRelativePath(
                [IO.Path]::GetFullPath($ReportRoot),
                [IO.Path]::GetFullPath($manifestPath)
            )
        }
        else {
            $null
        }
    }
    checks = [ordered]@{
        exactCanonicalConsumerInventory = $true
        consumerAgentCount = $liveConsumer.Count
        allConsumerAgentsCertified = $agentEvidence.Count -eq $expectedConsumer.Count
        runIdentityBound = $true
        freshEvidence = $true
        liveImmutableStateMatched = $true
    }
    agents = $agentEvidence
    failures = @()
}
$payload = Add-PayloadIntegrity -Payload $payload
$destination = Join-Path $ReportRoot "consumer-fleet"
$output = Join-Path $destination "$runId.json"
Write-AtomicJson -Path $output -Payload $payload -RunId $runId

Write-Host (
    "Fabrikam Consumer Products (synthetic demo) fleet certificate: " +
    "PASS consumer={0} runId={1} output={2}" -f
    $liveConsumer.Count,
    $runId,
    $output
)
