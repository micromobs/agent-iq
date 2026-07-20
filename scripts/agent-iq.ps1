[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("doctor", "install", "demo", "deploy", "package", "test", "status", "destroy")]
    [string]$Command = "doctor",
    [Alias("EnvironmentName")]
    [ValidatePattern("^[a-z0-9][a-z0-9-]{1,14}[a-z0-9]$")]
    [string]$Environment = "dev",
    [string]$SubscriptionId = "",
    [string]$Location = "swedencentral",
    [string]$ModelDeployment = "gpt-5.4-mini",
    [string]$EmbeddingDeployment = "text-embedding-3-large-gs",
    [ValidateSet("Basic", "Standard", "Standard2", "Standard3")]
    [string]$SearchSku = "Basic",
    [string]$ArtifactCompanionApplicationId = "",
    [string]$EnvironmentFile = "",
    [ValidatePattern("^[1-9][0-9]*\.[0-9]+\.[0-9]+$")]
    [string]$PluginVersion = "1.0.0",
    [string]$ImageTag = "",
    [switch]$Cloud,
    [switch]$Plan,
    [switch]$SkipBuild,
    [switch]$AllowAnonymousDemoConnector,
    [switch]$Yes,
    [switch]$Json
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = Split-Path -Parent $PSScriptRoot
$currentAzd = Join-Path $env:LOCALAPPDATA "Programs\Azure Dev CLI"
if (Test-Path $currentAzd) {
    $env:PATH = "$currentAzd;$env:PATH"
}
if ([string]::IsNullOrWhiteSpace($EnvironmentFile)) {
    $EnvironmentFile = Join-Path (
        Join-Path $env:LOCALAPPDATA "AgentIQ\environments\$Environment"
    ) "environment.ps1"
}

function Get-VersionTuple {
    param([AllowEmptyString()][string]$Text)

    $match = [regex]::Match($Text, "(?<major>\d+)\.(?<minor>\d+)(?:\.(?<patch>\d+))?")
    if (-not $match.Success) {
        return $null
    }
    $patch = if ($match.Groups["patch"].Success) {
        [int]$match.Groups["patch"].Value
    }
    else {
        0
    }
    return [version]::new(
        [int]$match.Groups["major"].Value,
        [int]$match.Groups["minor"].Value,
        $patch
    )
}

function Get-DoctorReport {
    param(
        [switch]$RequireCloud,
        [switch]$RequireDocker
    )

    $checks = [System.Collections.Generic.List[object]]::new()
    function Add-Check {
        param(
            [string]$Name,
            [bool]$Passed,
            [string]$Detail,
            [string]$Fix
        )
        $checks.Add([pscustomobject][ordered]@{
            name = $Name
            passed = $Passed
            detail = $Detail
            fix = $Fix
        })
    }

    $pwshVersion = $PSVersionTable.PSVersion
    Add-Check -Name "PowerShell" -Passed ($pwshVersion -ge [version]"7.4.0") `
        -Detail ([string]$pwshVersion) -Fix "Install PowerShell 7.4 or later."

    $git = Get-Command git -ErrorAction SilentlyContinue
    Add-Check -Name "Git" -Passed ($null -ne $git) `
        -Detail $(if ($git) { (& git --version) } else { "not found" }) `
        -Fix "Install Git: https://git-scm.com/downloads"

    $uv = Get-Command uv -ErrorAction SilentlyContinue
    Add-Check -Name "uv" -Passed ($null -ne $uv) `
        -Detail $(if ($uv) { (& uv --version) } else { "not found" }) `
        -Fix "Install uv: https://docs.astral.sh/uv/getting-started/installation/"

    $node = Get-Command node -ErrorAction SilentlyContinue
    $nodeText = if ($node) { [string](& node --version) } else { "" }
    $nodeVersion = Get-VersionTuple -Text $nodeText
    Add-Check -Name "Node.js" -Passed (
        $null -ne $nodeVersion -and $nodeVersion.Major -eq 24
    ) -Detail $(if ($nodeText) { $nodeText } else { "not found" }) `
        -Fix "Install Node.js 24 LTS: https://nodejs.org/"

    if ($RequireCloud) {
        $az = Get-Command az -ErrorAction SilentlyContinue
        $azVersionText = ""
        if ($az) {
            $azVersionObject = & az version --output json 2>$null | ConvertFrom-Json
            $azVersionText = [string]$azVersionObject."azure-cli"
        }
        $azVersion = Get-VersionTuple -Text $azVersionText
        Add-Check -Name "Azure CLI" -Passed (
            $null -ne $azVersion -and $azVersion -ge [version]"2.80.0"
        ) -Detail $(if ($azVersionText) { $azVersionText } else { "not found" }) `
            -Fix "Install Azure CLI 2.80+: https://aka.ms/installazurecliwindows"

        $azd = Get-Command azd -ErrorAction SilentlyContinue
        $azdText = if ($azd) {
            [string](& azd version 2>$null | Select-Object -First 1)
        }
        else {
            ""
        }
        $azdVersion = Get-VersionTuple -Text $azdText
        Add-Check -Name "Azure Developer CLI" -Passed (
            $null -ne $azdVersion -and $azdVersion -ge [version]"1.27.0"
        ) -Detail $(if ($azdText) { $azdText } else { "not found" }) `
            -Fix "Install azd 1.27+: https://aka.ms/install-azd"

        $azureAccount = $null
        if ($az) {
            $azureAccount = & az account show --output json --only-show-errors 2>$null |
                ConvertFrom-Json
        }
        Add-Check -Name "Azure login" -Passed ($null -ne $azureAccount) `
            -Detail $(if ($azureAccount) { [string]$azureAccount.user.name } else { "not signed in" }) `
            -Fix "Run: az login --use-device-code"
        if ($azureAccount) {
            Add-Check -Name "Azure subscription" -Passed (
                $azureAccount.state -ceq "Enabled"
            ) -Detail "$($azureAccount.name) ($($azureAccount.state))" `
                -Fix "Select or reactivate an Enabled subscription."
        }

        $azdAuthenticated = $false
        if ($azd) {
            & azd auth login --check-status *> $null
            $azdAuthenticated = $LASTEXITCODE -eq 0
        }
        Add-Check -Name "azd login" -Passed $azdAuthenticated `
            -Detail $(if ($azdAuthenticated) { "signed in" } else { "not signed in" }) `
            -Fix "Run: azd auth login --use-device-code"
    }

    if ($RequireDocker) {
        $docker = Get-Command docker -ErrorAction SilentlyContinue
        $dockerVersion = ""
        if ($docker) {
            $dockerVersion = [string](& docker version --format "{{.Server.Version}}" 2>$null)
        }
        Add-Check -Name "Docker" -Passed (-not [string]::IsNullOrWhiteSpace($dockerVersion)) `
            -Detail $(if ($dockerVersion) { $dockerVersion } else { "daemon unavailable" }) `
            -Fix "Install and start Docker Desktop: https://www.docker.com/products/docker-desktop/"
    }

    $failed = @($checks | Where-Object { -not $_.passed })
    return [pscustomobject][ordered]@{
        status = if ($failed.Count -eq 0) { "pass" } else { "fail" }
        checks = @($checks)
        failed = $failed.Count
    }
}

function Write-DoctorReport {
    param([Parameter(Mandatory = $true)][object]$Report)

    if ($Json) {
        $Report | ConvertTo-Json -Depth 6
        return
    }
    foreach ($check in $Report.checks) {
        $label = if ($check.passed) { "PASS" } else { "FAIL" }
        $color = if ($check.passed) { "Green" } else { "Red" }
        Write-Host ("[{0}] {1}: {2}" -f $label, $check.name, $check.detail) -ForegroundColor $color
        if (-not $check.passed) {
            Write-Host "       $($check.fix)" -ForegroundColor Yellow
        }
    }
}

function Assert-Doctor {
    param(
        [switch]$RequireCloud,
        [switch]$RequireDocker
    )

    $report = Get-DoctorReport -RequireCloud:$RequireCloud -RequireDocker:$RequireDocker
    Write-DoctorReport -Report $report
    if ($report.status -ne "pass") {
        throw "Agent IQ doctor found $($report.failed) blocking prerequisite(s)."
    }
}

function Connect-Azure {
    if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
        throw "Azure CLI is not installed. Run the doctor command for installation guidance."
    }
    if (-not (Get-Command azd -ErrorAction SilentlyContinue)) {
        throw "Azure Developer CLI is not installed. Run the doctor command for guidance."
    }
    $account = & az account show --output json --only-show-errors 2>$null | ConvertFrom-Json
    if ($null -eq $account) {
        & az login --use-device-code --only-show-errors | Out-Host
        if ($LASTEXITCODE -ne 0) {
            throw "Azure CLI login failed."
        }
    }
    if ($SubscriptionId) {
        & az account set --subscription $SubscriptionId --only-show-errors
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to select Azure subscription '$SubscriptionId'."
        }
    }
    $account = & az account show --output json --only-show-errors | ConvertFrom-Json
    if ($account.state -cne "Enabled") {
        throw "Azure subscription '$($account.name)' is $($account.state); deployment requires Enabled."
    }
    $script:SubscriptionId = [string]$account.id
    $env:AZURE_SUBSCRIPTION_ID = $script:SubscriptionId
    $env:AZURE_TENANT_ID = [string]$account.tenantId
    $env:AGENT_IQ_USE_CURRENT_AZURE_SESSION = "true"

    & azd auth login --check-status *> $null
    if ($LASTEXITCODE -ne 0) {
        & azd auth login --use-device-code --tenant-id $env:AZURE_TENANT_ID | Out-Host
        if ($LASTEXITCODE -ne 0) {
            throw "Azure Developer CLI login failed."
        }
    }
}

function Install-Dependencies {
    Assert-Doctor
    . (Join-Path $PSScriptRoot "dev_env.ps1")
    Push-Location $root
    try {
        & uv sync --all-groups --locked
        if ($LASTEXITCODE -ne 0) {
            throw "Dependency installation failed."
        }
    }
    finally {
        Pop-Location
    }
}

function Import-TeamEnvironment {
    if (-not (Test-Path -LiteralPath $EnvironmentFile -PathType Leaf)) {
        throw "Environment file is missing: $EnvironmentFile. Run the deploy command first."
    }
    . $EnvironmentFile
    if (
        [string]::IsNullOrWhiteSpace($script:SubscriptionId) -and
        -not [string]::IsNullOrWhiteSpace($env:AZURE_SUBSCRIPTION_ID)
    ) {
        $script:SubscriptionId = $env:AZURE_SUBSCRIPTION_ID
    }
    $env:AGENT_IQ_USE_CURRENT_AZURE_SESSION = "true"
}

function Get-EnvironmentOutputDirectory {
    return Split-Path -Parent $EnvironmentFile
}

function Assert-AnonymousDemoOptIn {
    if (-not $AllowAnonymousDemoConnector) {
        throw (
            "This preview creates an anonymously reachable connector with synthetic data. " +
            "Pass -AllowAnonymousDemoConnector only for an isolated personal demo. " +
            "It is not an organization-production authentication mode."
        )
    }
}

function Build-CoworkPackage {
    Assert-AnonymousDemoOptIn
    Import-TeamEnvironment
    $outputDirectory = Get-EnvironmentOutputDirectory
    $packageOutput = Join-Path $outputDirectory "cowork"
    $reportOutput = Join-Path $outputDirectory "reports\cowork"
    $mcpUrl = "$($env:AGENT_IQ_ORCHESTRATOR_BASE_URL)/mcp"

    Push-Location $root
    try {
        & uv run python -m agent_iq.deployments.cowork `
            --out $packageOutput `
            --report-root $reportOutput `
            --connector-url $mcpUrl `
            --manifest-id $env:AGENT_IQ_COWORK_APP_ID `
            --app-version $PluginVersion `
            --authorization-type None `
            --json | Out-Host
        if ($LASTEXITCODE -ne 0) {
            throw "Cowork package build failed."
        }
    }
    finally {
        Pop-Location
    }
    $builtPackage = Join-Path $packageOutput "agent-iq-canary.zip"
    $stablePackage = Join-Path $outputDirectory "agent-iq.zip"
    Copy-Item -LiteralPath $builtPackage -Destination $stablePackage -Force
    return $stablePackage
}

function Build-CopilotPlugin {
    Import-TeamEnvironment
    $outputDirectory = Get-EnvironmentOutputDirectory
    $pluginOutput = Join-Path $outputDirectory "copilot"
    $reportOutput = Join-Path $outputDirectory "reports\copilot"
    $mcpUrl = "$($env:AGENT_IQ_ORCHESTRATOR_BASE_URL)/mcp"

    Push-Location $root
    try {
        & uv run python -m agent_iq.deployments.copilot_plugin `
            --out $pluginOutput `
            --report-root $reportOutput `
            --mcp-url $mcpUrl `
            --json | Out-Host
        if ($LASTEXITCODE -ne 0) {
            throw "Copilot plugin build failed."
        }
    }
    finally {
        Pop-Location
    }
    return Join-Path $pluginOutput "agent-iq-plugin"
}

function Test-RemoteEnvironment {
    Import-TeamEnvironment
    $mcpUrl = "$($env:AGENT_IQ_ORCHESTRATOR_BASE_URL)/mcp"
    Push-Location $root
    try {
        & uv run python .\scripts\probe_mcp_contract.py --mcp-url $mcpUrl
        if ($LASTEXITCODE -ne 0) {
            throw "MCP resource compatibility probe failed."
        }
        & (Join-Path $PSScriptRoot "probe_cowork_connector.ps1") `
            -McpUrl $mcpUrl `
            -NoReport
        if ($LASTEXITCODE -ne 0) {
            throw "Consumer-product Cowork scenario probe failed."
        }
        & (Join-Path $PSScriptRoot "probe_cowork_connector.ps1") `
            -McpUrl $mcpUrl `
            -Intent "My VPN is down." `
            -ExpectedAgent "employee-it-helpdesk" `
            -ExpectedSourceId "employee-vpn-recovery-guidance" `
            -NoReport
        if ($LASTEXITCODE -ne 0) {
            throw "VPN Cowork scenario probe failed."
        }
    }
    finally {
        Pop-Location
    }
}

function Invoke-Deployment {
    if ($Plan) {
        if ([string]::IsNullOrWhiteSpace($SubscriptionId)) {
            $account = & az account show --output json --only-show-errors 2>$null | ConvertFrom-Json
            if ($null -eq $account) {
                throw "Use -SubscriptionId with -Plan, or sign in with Azure CLI."
            }
            $script:SubscriptionId = [string]$account.id
        }
        & (Join-Path $PSScriptRoot "provision_team_azure.ps1") `
            -EnvironmentName $Environment `
            -SubscriptionId $script:SubscriptionId `
            -Location $Location `
            -ModelDeployment $ModelDeployment `
            -EmbeddingDeployment $EmbeddingDeployment `
            -SearchSku $SearchSku `
            -ArtifactCompanionApplicationId $ArtifactCompanionApplicationId `
            -OutputDirectory (Split-Path -Parent $EnvironmentFile) `
            -Plan
        Write-Host "`nDeployment phases: foundation -> bootstrap host -> 24 agents -> strict host -> package -> probes"
        Write-Host "No Azure resource was changed." -ForegroundColor Green
        return
    }

    Assert-AnonymousDemoOptIn
    Write-Warning (
        "Anonymous demo connector explicitly enabled. Keep the environment isolated, " +
        "use synthetic data only, and destroy it after testing."
    )
    Connect-Azure
    Assert-Doctor -RequireCloud -RequireDocker:(-not $SkipBuild)
    if ($SkipBuild -and [string]::IsNullOrWhiteSpace($ImageTag)) {
        throw "-SkipBuild requires -ImageTag for an existing ACR image."
    }
    if (-not $Yes) {
        Write-Host ""
        Write-Host "COST WARNING" -ForegroundColor Yellow
        Write-Host (
            "This deploy creates billable Foundry model deployments, Azure AI Search, " +
            "Container Registry, Container Apps, Log Analytics, and hosted agents."
        ) -ForegroundColor Yellow
        $confirmation = Read-Host "Type DEPLOY to continue"
        if ($confirmation -cne "DEPLOY") {
            throw "Deployment cancelled."
        }
    }

    Install-Dependencies
    & azd extension show azure.ai.agents *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Installing azd azure.ai.agents extension..." -ForegroundColor Cyan
        & azd extension install azure.ai.agents
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to install the azd azure.ai.agents extension."
        }
    }

    $provisioned = & (Join-Path $PSScriptRoot "provision_team_azure.ps1") `
        -EnvironmentName $Environment `
        -SubscriptionId $script:SubscriptionId `
        -Location $Location `
        -ModelDeployment $ModelDeployment `
        -EmbeddingDeployment $EmbeddingDeployment `
        -SearchSku $SearchSku `
        -ArtifactCompanionApplicationId $ArtifactCompanionApplicationId `
        -OutputDirectory (Split-Path -Parent $EnvironmentFile)
    if ($null -eq $provisioned) {
        throw "Azure foundation provisioning did not return an environment record."
    }
    Import-TeamEnvironment

    if ([string]::IsNullOrWhiteSpace($ImageTag)) {
        $ImageTag = "team-" + [DateTime]::UtcNow.ToString("yyyyMMddHHmmss")
    }
    $orchestratorArguments = @{
        ResourceGroup = $env:AGENT_IQ_RESOURCE_GROUP
        ContainerAppsEnvironment = $env:AGENT_IQ_CONTAINER_APPS_ENVIRONMENT
        AcrName = $env:AGENT_IQ_ACR_NAME
        AiAccount = $env:AGENT_IQ_AZURE_AI_ACCOUNT
        FoundryProject = $env:AGENT_IQ_AZURE_AI_PROJECT
        SearchService = $env:AGENT_IQ_SEARCH_SERVICE
        FoundryProjectEndpoint = $env:AGENT_IQ_FOUNDRY_PROJECT_ENDPOINT
        AzureOpenAiEndpoint = $env:AGENT_IQ_AZURE_OPENAI_ENDPOINT
        EmbeddingDeployment = $env:AGENT_IQ_AZURE_EMBEDDING_DEPLOYMENT
        AppName = $env:AGENT_IQ_ORCHESTRATOR_APP_NAME
        ArtifactCompanionApplicationId = (
            $env:AGENT_IQ_ARTIFACT_COMPANION_APPLICATION_ID
        )
        ArtifactCompanionTenantId = $env:AGENT_IQ_ARTIFACT_COMPANION_TENANT_ID
        ImageTag = $ImageTag
    }
    if ($SkipBuild) {
        $orchestratorArguments.SkipBuild = $true
    }
    $orchestratorArguments.Bootstrap = $true
    Write-Host "`n[1/5] Deploying bootstrap orchestrator..." -ForegroundColor Cyan
    & (Join-Path $PSScriptRoot "deploy_orchestrator_canary.ps1") @orchestratorArguments

    Write-Host "`n[2/5] Deploying the canonical hosted-agent fleet..." -ForegroundColor Cyan
    & (Join-Path $PSScriptRoot "deploy_consumer_fleet.ps1") `
        -ProjectEndpoint $env:AGENT_IQ_FOUNDRY_PROJECT_ENDPOINT `
        -ResourceGroup $env:AGENT_IQ_RESOURCE_GROUP `
        -AccountName $env:AGENT_IQ_AZURE_AI_ACCOUNT `
        -ProjectName $env:AGENT_IQ_AZURE_AI_PROJECT `
        -SearchResourceGroup $env:AGENT_IQ_SEARCH_RESOURCE_GROUP `
        -SearchService $env:AGENT_IQ_SEARCH_SERVICE `
        -Model $env:AGENT_IQ_AZURE_MODEL_DEPLOYMENT `
        -OrchestratorBaseUrl $env:AGENT_IQ_ORCHESTRATOR_BASE_URL `
        -OrchestratorAppName $env:AGENT_IQ_ORCHESTRATOR_APP_NAME `
        -CompanionApplicationId $env:AGENT_IQ_ARTIFACT_COMPANION_APPLICATION_ID `
        -CompanionTenantId $env:AGENT_IQ_ARTIFACT_COMPANION_TENANT_ID

    Write-Host "`n[3/5] Promoting the strict orchestrator..." -ForegroundColor Cyan
    $orchestratorArguments.Remove("Bootstrap")
    $orchestratorArguments.SkipBuild = $true
    & (Join-Path $PSScriptRoot "deploy_orchestrator_canary.ps1") @orchestratorArguments

    Write-Host "`n[4/5] Building tenant-specific host packages..." -ForegroundColor Cyan
    $coworkPackagePath = Build-CoworkPackage
    $copilotPluginPath = Build-CopilotPlugin

    Write-Host "`n[5/5] Running live connector and scenario probes..." -ForegroundColor Cyan
    Test-RemoteEnvironment

    Write-Host "`nAgent IQ deployment complete." -ForegroundColor Green
    Write-Host "Cowork package: $coworkPackagePath"
    Write-Host "Copilot plugin: $copilotPluginPath"
    Write-Host "MCP endpoint: $($env:AGENT_IQ_ORCHESTRATOR_BASE_URL)/mcp"
    Write-Host "Install personally: atk install --file-path '$coworkPackagePath' --scope Personal"
    Write-Host "Try: My VPN is down."
    Write-Host "Try: How much vacation do I have remaining this year?"
}

function Show-Status {
    Import-TeamEnvironment
    Connect-Azure
    $healthUrl = "$($env:AGENT_IQ_ORCHESTRATOR_BASE_URL)/healthz"
    $readyUrl = "$($env:AGENT_IQ_ORCHESTRATOR_BASE_URL)/readyz"
    $health = Invoke-WebRequest -Uri $healthUrl -SkipHttpErrorCheck -TimeoutSec 30
    $ready = Invoke-WebRequest -Uri $readyUrl -SkipHttpErrorCheck -TimeoutSec 30
    $result = [pscustomobject][ordered]@{
        environment = $Environment
        subscriptionId = $env:AZURE_SUBSCRIPTION_ID
        resourceGroup = $env:AGENT_IQ_RESOURCE_GROUP
        baseUrl = $env:AGENT_IQ_ORCHESTRATOR_BASE_URL
        healthStatus = [int]$health.StatusCode
        readinessStatus = [int]$ready.StatusCode
        packagePath = Join-Path (Get-EnvironmentOutputDirectory) "agent-iq.zip"
        copilotPluginPath = Join-Path (
            Get-EnvironmentOutputDirectory
        ) "copilot\agent-iq-plugin"
    }
    if ($Json) {
        $result | ConvertTo-Json
    }
    else {
        $result | Format-List
    }
}

function Destroy-TeamEnvironment {
    $recordPath = Join-Path (Get-EnvironmentOutputDirectory) "deployment.json"
    if (-not (Test-Path -LiteralPath $recordPath -PathType Leaf)) {
        throw "Deployment record is missing: $recordPath."
    }
    try {
        $record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
    }
    catch {
        throw "Deployment record is not valid JSON: $recordPath."
    }
    if (
        "subscriptionId" -notin $record.PSObject.Properties.Name -or
        "resourceGroup" -notin $record.PSObject.Properties.Name
    ) {
        throw "Deployment record must identify its subscription and resource group."
    }
    $recordSubscriptionGuid = [guid]::Empty
    if (
        -not [guid]::TryParseExact(
            [string]$record.subscriptionId,
            "D",
            [ref]$recordSubscriptionGuid
        ) -or
        $recordSubscriptionGuid -eq [guid]::Empty
    ) {
        throw "Deployment record contains an invalid subscription ID."
    }
    $recordSubscriptionId = $recordSubscriptionGuid.ToString("D")
    $recordResourceGroup = [string]$record.resourceGroup
    if ([string]::IsNullOrWhiteSpace($recordResourceGroup)) {
        throw "Deployment record contains an invalid resource group."
    }
    if (-not [string]::IsNullOrWhiteSpace($SubscriptionId)) {
        $requestedSubscriptionGuid = [guid]::Empty
        if (
            -not [guid]::TryParseExact(
                $SubscriptionId,
                "D",
                [ref]$requestedSubscriptionGuid
            ) -or
            $requestedSubscriptionGuid -eq [guid]::Empty
        ) {
            throw "SubscriptionId must be a canonical nonzero UUID."
        }
        if ($requestedSubscriptionGuid.ToString("D") -cne $recordSubscriptionId) {
            throw (
                "Requested subscription '$SubscriptionId' does not match deployment " +
                "record subscription '$recordSubscriptionId'."
            )
        }
    }
    $script:SubscriptionId = $recordSubscriptionId
    Import-TeamEnvironment
    if (
        [string]$env:AZURE_SUBSCRIPTION_ID -cne $recordSubscriptionId -or
        [string]$env:AGENT_IQ_RESOURCE_GROUP -cne $recordResourceGroup
    ) {
        throw "Environment file does not match the deployment record; refusing teardown."
    }
    Connect-Azure
    $deleteArtifactCompanion = $false
    if (
        $record.artifactCompanionManaged -eq $true -and
        $record.artifactCompanionApplicationId
    ) {
        $ownershipMarker = [string]$record.artifactCompanionOwnershipMarker
        if ([string]::IsNullOrWhiteSpace($ownershipMarker)) {
            Write-Warning (
                "The deployment record has no Entra ownership marker. " +
                "The artifact companion application will not be deleted."
            )
        }
        else {
            $appJson = & az ad app show `
                --id $record.artifactCompanionApplicationId `
                --query "{appId:appId,ownershipMarker:serviceManagementReference}" `
                --output json `
                --only-show-errors 2>$null
            if ($LASTEXITCODE -eq 0 -and $appJson) {
                $app = $appJson | ConvertFrom-Json
                $deleteArtifactCompanion = (
                    [string]$app.appId -ceq [string]$record.artifactCompanionApplicationId -and
                    [string]$app.ownershipMarker -ceq $ownershipMarker
                )
            }
            if (-not $deleteArtifactCompanion) {
                Write-Warning (
                    "The live Entra application does not carry this environment's " +
                    "ownership marker and will not be deleted."
                )
            }
        }
    }
    if (-not $Yes) {
        Write-Host (
            "This permanently deletes resource group $recordResourceGroup in " +
            "subscription $recordSubscriptionId."
        ) `
            -ForegroundColor Yellow
        $confirmation = Read-Host "Type DESTROY to continue"
        if ($confirmation -cne "DESTROY") {
            throw "Teardown cancelled."
        }
    }
    & az group delete `
        --name $recordResourceGroup `
        --subscription $recordSubscriptionId `
        --yes `
        --no-wait `
        --only-show-errors
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to start Azure resource-group deletion."
    }
    if ($deleteArtifactCompanion) {
        & az ad app delete --id $record.artifactCompanionApplicationId --only-show-errors
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Resource deletion started, but the managed Entra application was not deleted."
        }
    }
    Write-Host "Teardown started. Azure may take several minutes to finish." -ForegroundColor Green
}

switch ($Command) {
    "doctor" {
        $report = Get-DoctorReport -RequireCloud:$Cloud -RequireDocker:$Cloud
        Write-DoctorReport -Report $report
        if ($report.status -ne "pass") {
            exit 1
        }
    }
    "install" {
        Install-Dependencies
    }
    "demo" {
        Install-Dependencies
        & (Join-Path $PSScriptRoot "run_employee_showcase.ps1")
    }
    "deploy" {
        Invoke-Deployment
    }
    "package" {
        Assert-AnonymousDemoOptIn
        Install-Dependencies
        $coworkPackagePath = Build-CoworkPackage
        $copilotPluginPath = Build-CopilotPlugin
        Write-Host "Cowork package: $coworkPackagePath" -ForegroundColor Green
        Write-Host "Copilot plugin: $copilotPluginPath" -ForegroundColor Green
    }
    "test" {
        Install-Dependencies
        if (Test-Path -LiteralPath $EnvironmentFile -PathType Leaf) {
            Test-RemoteEnvironment
        }
        else {
            & (Join-Path $PSScriptRoot "run_offline_evals.ps1")
            if ($LASTEXITCODE -ne 0) {
                throw "Offline Agent IQ scenarios failed."
            }
        }
    }
    "status" {
        Show-Status
    }
    "destroy" {
        Destroy-TeamEnvironment
    }
}
