[CmdletBinding()]
param(
    [ValidateSet("Offline", "Preflight", "LiveCanary")]
    [string]$Mode = "Offline",
    [switch]$BuildLocalArtifacts,
    [switch]$ProbeLive,
    [switch]$ProbeCopilotHosts,
    [switch]$PlanDeployment,
    [Alias("Deploy")]
    [switch]$DeployOrchestratorCanary,
    [switch]$SkipDeploymentBuild,
    [switch]$CanvasFallback,
    [switch]$OpenBrowser,
    [ValidateRange(1024, 65535)]
    [int]$CanvasPort = 5173,
    [string]$BaseUrl = $env:AGENT_IQ_ORCHESTRATOR_BASE_URL,
    [string]$ResourceGroup = $env:AGENT_IQ_AZURE_RESOURCE_GROUP,
    [string]$ContainerAppsEnvironment = $env:AGENT_IQ_AZURE_CONTAINER_APPS_ENVIRONMENT,
    [string]$AcrName = $env:AGENT_IQ_AZURE_CONTAINER_REGISTRY,
    [string]$AiAccount = $env:AGENT_IQ_AZURE_AI_ACCOUNT,
    [string]$FoundryProject = $env:AGENT_IQ_AZURE_AI_PROJECT,
    [string]$SearchService = $env:AGENT_IQ_SEARCH_SERVICE,
    [string]$FoundryProjectEndpoint = $env:AGENT_IQ_FOUNDRY_PROJECT_ENDPOINT,
    [string]$AzureOpenAiEndpoint = $env:AGENT_IQ_AZURE_OPENAI_ENDPOINT,
    [string]$AppInsightsName = $env:AGENT_IQ_AZURE_APP_INSIGHTS,
    [string]$EmbeddingDeployment = "text-embedding-3-large-gs",
    [string]$AppName = "agent-iq-orchestrator-canary",
    [string]$ImageTag = "",
    [string]$DeploymentReportPath = $env:AGENT_IQ_ORCHESTRATOR_DEPLOYMENT_REPORT
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$canvasRoot = Join-Path $root "apps\canvas"
$deploymentScript = Join-Path $PSScriptRoot "deploy_orchestrator_canary.ps1"
$verifyScript = Join-Path $PSScriptRoot "verify.ps1"
$scenarioPath = Join-Path $root "evals\launch_command_center.json"
$deploymentReportRoot = Join-Path $root "reports\deployments\container-apps\orchestrator-canary"
$deploymentReportPattern = '^orchestrator-canary-run-\d{8}T\d{13}Z-[0-9a-f]{32}\.json$'

if (-not (Test-Path -LiteralPath $scenarioPath -PathType Leaf)) {
    throw "Launch command-center scenario is missing at $scenarioPath."
}
$scenario = Get-Content -LiteralPath $scenarioPath -Raw -Encoding utf8 | ConvertFrom-Json
if (
    $scenario.schemaVersion -ne "1.0" -or
    [string]::IsNullOrWhiteSpace([string]$scenario.intent) -or
    $scenario.rehearsalPrompts.Count -ne 5
) {
    throw "Launch command-center scenario is malformed."
}
$grandFinalePrompt = [string]$scenario.intent
$rehearsalPrompts = @($scenario.rehearsalPrompts | ForEach-Object { [string]$_ })

function Get-ExecutablePath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    $command = @(Get-Command $Name -CommandType Application -ErrorAction Stop) |
        Where-Object { $_.Path -and (Test-Path -LiteralPath $_.Path -PathType Leaf) } |
        Select-Object -First 1
    if (-not $command) {
        throw "Required executable '$Name' was not resolved."
    }
    return [string]$command.Path
}

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Description,
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [string[]]$CommandArguments = @()
    )

    Write-Host "[updated-demo] $Description"
    & $FilePath @CommandArguments
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "$Description failed with exit code $exitCode."
    }
}

function Write-DemoPrompts {
    Write-Host ""
    Write-Host "Grand finale (exact launch-readiness intent):" -ForegroundColor Cyan
    Write-Host $grandFinalePrompt
    Write-Host ""
    Write-Host "Five rehearsal prompts (exact current skill wording):" -ForegroundColor Cyan
    for ($index = 0; $index -lt $rehearsalPrompts.Count; $index++) {
        Write-Host ("  {0}. {1}" -f ($index + 1), $rehearsalPrompts[$index])
    }
}

function Write-PlaneBoundary {
    Write-Host ""
    Write-Host "Demo evidence boundary" -ForegroundColor Cyan
    $planes = @(
        [pscustomobject]@{
            Plane = "Foundry native A2A / Foundry IQ"
            Status = "LIVE only when a live canary probe passes"
            Boundary = "No live call in Offline or Preflight"
        },
        [pscustomobject]@{
            Plane = "Cowork remote MCP"
            Status = "LIVE only when the remote certificate passes"
            Boundary = "Current Demo 1 connector auth is None"
        },
        [pscustomobject]@{
            Plane = "Work IQ"
            Status = "LABELLED FIXTURE in the command-center replay"
            Boundary = "Synthetic IDs and metadata only; no M365 source body"
        },
        [pscustomobject]@{
            Plane = "SAP plant and line"
            Status = "LABELLED FIXTURE"
            Boundary = "DE-PLANT-FIXTURE-01 and LINE-FIXTURE-01 are not live SAP"
        },
        [pscustomobject]@{
            Plane = "Canvas replay"
            Status = "LABELLED FIXTURE / deterministic"
            Boundary = "Browser fallback is separate from Copilot host acceptance"
        },
        [pscustomobject]@{
            Plane = "Copilot Desktop Present mode"
            Status = "BLOCKED"
            Boundary = "Manual Present/fullscreen acceptance is not certified here"
        }
    )
    $planes | Format-Table -Property Plane, Status, Boundary -Wrap | Out-Host
}

function Invoke-LocalContracts {
    param(
        [Parameter(Mandatory = $true)]
        [string]$UvPath,
        [switch]$Offline
    )

    $uvRunPrefix = @("run")
    if ($Offline) {
        $uvRunPrefix += @("--offline", "--no-sync")
    }

    if ($BuildLocalArtifacts) {
        Invoke-CheckedCommand `
            -Description "Build and validate the Cowork canary package" `
            -FilePath $UvPath `
            -CommandArguments @(
                $uvRunPrefix + @("python", "deployments\cowork\build.py", "--json")
            )
        Invoke-CheckedCommand `
            -Description "Build and validate the Copilot plugin package" `
            -FilePath $UvPath `
            -CommandArguments @(
                $uvRunPrefix + @("python", "deployments\copilot\build.py", "--json")
            )
        Invoke-CheckedCommand `
            -Description "Regenerate the deterministic Canvas replay" `
            -FilePath $UvPath `
            -CommandArguments @(
                $uvRunPrefix + @("python", "scripts\generate_canvas_demo.py")
            )
        $npmPath = Get-ExecutablePath -Name "npm.cmd"
        Push-Location $canvasRoot
        try {
            Invoke-CheckedCommand `
                -Description "Build the current Canvas web bundle" `
                -FilePath $npmPath `
                -CommandArguments @("run", "build")
            Invoke-CheckedCommand `
                -Description "Verify the deterministic Canvas web bundle" `
                -FilePath $npmPath `
                -CommandArguments @("run", "build:check")
        }
        finally {
            Pop-Location
        }
    }
    else {
        $packageScratch = Join-Path (
            [System.IO.Path]::GetTempPath()
        ) "agent-iq-updated-demo-packages-$PID"
        try {
            $coworkScratch = Join-Path $packageScratch "cowork"
            Invoke-CheckedCommand `
                -Description "Build the Cowork canary package in clean scratch" `
                -FilePath $UvPath `
                -CommandArguments @(
                    $uvRunPrefix + @(
                        "python",
                        "deployments\cowork\build.py",
                        "--out",
                        $coworkScratch,
                        "--json"
                    )
                )
            Invoke-CheckedCommand `
                -Description "Verify the Cowork canary package in clean scratch" `
                -FilePath $UvPath `
                -CommandArguments @(
                    $uvRunPrefix + @(
                        "python",
                        "deployments\cowork\build.py",
                        "--out",
                        $coworkScratch,
                        "--check",
                        "--json"
                    )
                )
            $copilotScratch = Join-Path $packageScratch "copilot"
            Invoke-CheckedCommand `
                -Description "Build the Copilot plugin package in clean scratch" `
                -FilePath $UvPath `
                -CommandArguments @(
                    $uvRunPrefix + @(
                        "python",
                        "deployments\copilot\build.py",
                        "--out",
                        $copilotScratch,
                        "--json"
                    )
                )
            Invoke-CheckedCommand `
                -Description "Verify the Copilot plugin package in clean scratch" `
                -FilePath $UvPath `
                -CommandArguments @(
                    $uvRunPrefix + @(
                        "python",
                        "deployments\copilot\build.py",
                        "--out",
                        $copilotScratch,
                        "--check",
                        "--json"
                    )
                )
        }
        finally {
            if (Test-Path -LiteralPath $packageScratch) {
                Remove-Item -LiteralPath $packageScratch -Recurse -Force
            }
        }
        Invoke-CheckedCommand `
            -Description "Validate the deterministic Canvas replay" `
            -FilePath $UvPath `
            -CommandArguments @(
                $uvRunPrefix + @("python", "scripts\generate_canvas_demo.py", "--check")
            )
    }
}

function Assert-DeploymentCoordinates {
    $required = @(
        [pscustomobject]@{ Name = "ResourceGroup"; Value = $ResourceGroup },
        [pscustomobject]@{ Name = "ContainerAppsEnvironment"; Value = $ContainerAppsEnvironment },
        [pscustomobject]@{ Name = "AcrName"; Value = $AcrName },
        [pscustomobject]@{ Name = "AiAccount"; Value = $AiAccount },
        [pscustomobject]@{ Name = "FoundryProject"; Value = $FoundryProject },
        [pscustomobject]@{ Name = "SearchService"; Value = $SearchService },
        [pscustomobject]@{ Name = "FoundryProjectEndpoint"; Value = $FoundryProjectEndpoint },
        [pscustomobject]@{ Name = "AzureOpenAiEndpoint"; Value = $AzureOpenAiEndpoint }
    )
    $missing = @(
        $required |
            Where-Object { [string]::IsNullOrWhiteSpace([string]$_.Value) } |
            ForEach-Object { $_.Name }
    )
    if ($missing.Count -gt 0) {
        throw (
            "Deployment planning requires non-secret coordinates: " +
            ($missing -join ", ") +
            ". Supply parameters or AGENT_IQ_* environment values."
        )
    }
}

function Get-DeploymentReportFiles {
    return @(
        Get-ChildItem -LiteralPath $deploymentReportRoot -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -cmatch $deploymentReportPattern } |
            Sort-Object LastWriteTimeUtc, Name -Descending
    )
}

function Resolve-ExplicitDeploymentReport {
    param(
        [string]$CandidatePath = "",
        [System.IO.FileInfo[]]$PriorReports = @(),
        [switch]$AfterDeployment
    )

    if ([string]::IsNullOrWhiteSpace($CandidatePath)) {
        if (-not $AfterDeployment) {
            throw (
                "Live transport certification requires -DeploymentReportPath or a report " +
                "created by -DeployOrchestratorCanary."
            )
        }
        $priorNames = @($PriorReports | ForEach-Object { $_.Name })
        $newReports = @(
            Get-DeploymentReportFiles |
                Where-Object { $priorNames -notcontains $_.Name }
        )
        if ($newReports.Count -ne 1) {
            throw (
                "Deployment did not produce exactly one new orchestrator-canary-run report; " +
                "refusing to guess a deployment evidence file."
            )
        }
        $CandidatePath = $newReports[0].FullName
    }

    try {
        $fullPath = [System.IO.Path]::GetFullPath($CandidatePath)
        $reportRootFullPath = [System.IO.Path]::GetFullPath($deploymentReportRoot)
    }
    catch {
        throw "Deployment report path is not a valid local path."
    }
    $parentPath = [System.IO.Path]::GetDirectoryName($fullPath)
    $fileName = [System.IO.Path]::GetFileName($fullPath)
    if (
        -not [string]::Equals(
            $parentPath,
            $reportRootFullPath,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -or
        $fileName -eq "latest.json" -or
        -not ($fileName -cmatch $deploymentReportPattern)
    ) {
        throw (
            "Deployment report must be an explicit run-specific file in " +
            "$deploymentReportRoot matching orchestrator-canary-run-*.json."
        )
    }
    if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
        throw "Deployment report does not exist: $fullPath."
    }
    return $fullPath
}

function Invoke-CanaryDeployment {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PwshPath
    )

    Assert-DeploymentCoordinates
    $deploymentArguments = @(
        "-NoProfile", "-File", $deploymentScript,
        "-ResourceGroup", $ResourceGroup,
        "-ContainerAppsEnvironment", $ContainerAppsEnvironment,
        "-AcrName", $AcrName,
        "-AiAccount", $AiAccount,
        "-FoundryProject", $FoundryProject,
        "-SearchService", $SearchService,
        "-FoundryProjectEndpoint", $FoundryProjectEndpoint,
        "-AzureOpenAiEndpoint", $AzureOpenAiEndpoint,
        "-EmbeddingDeployment", $EmbeddingDeployment,
        "-AppName", $AppName
    )
    if (-not [string]::IsNullOrWhiteSpace($AppInsightsName)) {
        $deploymentArguments += @("-AppInsightsName", $AppInsightsName)
    }
    if (-not [string]::IsNullOrWhiteSpace($ImageTag)) {
        $deploymentArguments += @("-ImageTag", $ImageTag)
    }
    if ($SkipDeploymentBuild) {
        $deploymentArguments += "-SkipBuild"
    }
    if ($PlanDeployment) {
        $deploymentArguments += "-WhatIf"
        Invoke-CheckedCommand `
            -Description "Show the non-mutating orchestrator canary deployment plan" `
            -FilePath $PwshPath `
            -CommandArguments $deploymentArguments
    }
    else {
        Write-Warning (
            "Azure mutation was explicitly requested with -DeployOrchestratorCanary. " +
            "The existing deployment script owns login, RBAC, image, and Container App changes."
        )
        Invoke-CheckedCommand `
            -Description "Deploy the explicitly requested orchestrator canary" `
            -FilePath $PwshPath `
            -CommandArguments $deploymentArguments
    }
}

function Invoke-LiveCertificates {
    param(
        [Parameter(Mandatory = $true)]
        [string]$UvPath,
        [Parameter(Mandatory = $true)]
        [string]$DeploymentReportPath
    )

    if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
        throw "Live probing requires -BaseUrl or AGENT_IQ_ORCHESTRATOR_BASE_URL."
    }
    if ([string]::IsNullOrWhiteSpace($ResourceGroup)) {
        throw "Live transport certification requires -ResourceGroup or AGENT_IQ_AZURE_RESOURCE_GROUP."
    }
    if ([string]::IsNullOrWhiteSpace($AppName)) {
        throw "Live transport certification requires a non-empty -AppName."
    }
    $normalizedBaseUrl = $BaseUrl.TrimEnd("/")
    $orchestratorArguments = @(
        "run", "python", "scripts\certify_orchestrator_canary.py",
        "--base-url", $normalizedBaseUrl,
        "--resource-group", $ResourceGroup,
        "--app-name", $AppName,
        "--deployment-report", $DeploymentReportPath
    )
    if (-not [string]::IsNullOrWhiteSpace($AppInsightsName)) {
        $orchestratorArguments += @(
            "--app-insights", $AppInsightsName
        )
    }
    Invoke-CheckedCommand `
        -Description "Certify HTTP, MCP, and native A2A transport parity (live, read-only)" `
        -FilePath $UvPath `
        -CommandArguments $orchestratorArguments

    $launchArguments = @(
        "run", "python", "scripts\certify_launch_command_center.py",
        "--base-url", $normalizedBaseUrl
    )
    Invoke-CheckedCommand `
        -Description "Certify the five-agent launch command center (live, read-only)" `
        -FilePath $UvPath `
        -CommandArguments $launchArguments
}

function Invoke-CopilotHostCertificates {
    param(
        [Parameter(Mandatory = $true)]
        [string]$UvPath
    )

    Invoke-CheckedCommand `
        -Description "Probe GitHub Copilot plugin loading and MCP registration" `
        -FilePath $UvPath `
        -CommandArguments @(
            "run", "python", "scripts\probe_copilot_plugin.py"
        )
    Invoke-CheckedCommand `
        -Description "Probe GitHub Copilot Canvas registration" `
        -FilePath $UvPath `
        -CommandArguments @(
            "run", "python", "scripts\probe_copilot_canvas.py"
        )
}

function Start-CanvasFallback {
    param(
        [Parameter(Mandatory = $true)]
        [string]$NodePath
    )

    $vitePath = Join-Path $canvasRoot "node_modules\vite\bin\vite.js"
    $replayPath = Join-Path $canvasRoot "public\demo\launch-command-center.agui.json"
    if (-not (Test-Path -LiteralPath $vitePath -PathType Leaf)) {
        throw "Canvas Vite executable is missing. Install apps\canvas dependencies first."
    }
    if (-not (Test-Path -LiteralPath $replayPath -PathType Leaf)) {
        throw "Canvas Agent IQ replay is missing at $replayPath."
    }

    $expectedReplay = Get-Content -LiteralPath $replayPath -Raw -Encoding utf8 | ConvertFrom-Json
    $canvasUrl = "http://127.0.0.1:$CanvasPort/"
    $process = $null
    try {
        $quotedVitePath = '"' + $vitePath.Replace('"', '\"') + '"'
        $process = Start-Process `
            -FilePath $NodePath `
            -ArgumentList @(
                $quotedVitePath,
                "--host", "127.0.0.1",
                "--port", [string]$CanvasPort,
                "--strictPort"
            ) `
            -WorkingDirectory $canvasRoot `
            -PassThru

        $response = $null
        $deadline = [DateTime]::UtcNow.AddSeconds(20)
        while ($null -eq $response -and [DateTime]::UtcNow -lt $deadline) {
            if ($process.HasExited) {
                throw (
                    "Canvas browser fallback exited before serving $canvasUrl. " +
                    "The requested port may already be occupied."
                )
            }
            try {
                $candidate = Invoke-WebRequest `
                    -Uri $canvasUrl `
                    -UseBasicParsing `
                    -TimeoutSec 5
                if ($candidate.StatusCode -ge 200 -and $candidate.StatusCode -lt 400) {
                    $response = $candidate
                }
            }
            catch {
                Start-Sleep -Milliseconds 250
            }
        }
        if ($null -eq $response) {
            throw "Canvas browser fallback did not become ready at $canvasUrl."
        }
        if ($response.Content -notmatch "Agent IQ Command Center") {
            throw "Canvas browser fallback returned an unexpected application."
        }

        if ($process.HasExited) {
            throw "Canvas browser fallback exited before the Agent IQ replay was validated."
        }
        $replayResponse = Invoke-WebRequest `
            -Uri "${canvasUrl}demo/launch-command-center.agui.json" `
            -Headers @{ Accept = "application/json" } `
            -UseBasicParsing `
            -TimeoutSec 5
        if ($replayResponse.StatusCode -lt 200 -or $replayResponse.StatusCode -ge 400) {
            throw "Canvas Agent IQ replay returned HTTP $($replayResponse.StatusCode)."
        }
        $replay = $replayResponse.Content | ConvertFrom-Json
        $replayText = $replayResponse.Content
        $expectedEventCount = [int]$expectedReplay.eventCount
        $expectedCanonicalEventCount = [int]$expectedReplay.canonicalEventCount
        $expectedAgentIds = @(
            "consumer-formulation-rd",
            "consumer-brand-to-brand-compare",
            "consumer-demand-forecasting",
            "consumer-manufacturing-operations",
            "consumer-regulatory-affairs"
        )
        if (
            $replay.executionId -ne "demo-eu-launch-command-center" -or
            $replay.eventCount -ne $expectedEventCount -or
            $replay.canonicalEventCount -ne $expectedCanonicalEventCount -or
            @($replay.events).Count -ne $expectedEventCount -or
            $replayText -notmatch "Conditional go" -or
            $replayText -notmatch '"type"\s*:\s*"RUN_STARTED"'
        ) {
            throw "Canvas browser fallback did not serve the expected Agent IQ replay."
        }
        foreach ($agentId in $expectedAgentIds) {
            if ($replayText -notmatch [regex]::Escape($agentId)) {
                throw "Canvas Agent IQ replay is missing expected agent '$agentId'."
            }
        }
    }
    catch {
        if ($null -ne $process) {
            try {
                $process.Refresh()
                if (-not $process.HasExited) {
                    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
                }
            }
            catch {
                # The child may have exited while the readiness failure was being handled.
            }
        }
        throw
    }
    # KeepCanvasServer semantics: leave the verified child running for browser acceptance.
    Write-Host "Canvas browser fallback is ready: $canvasUrl" -ForegroundColor Green
    Write-Host "Copilot Desktop Present mode remains BLOCKED; this is standalone browser acceptance."
    Write-Host "Canvas dev-server PID: $($process.Id)"
    if ($OpenBrowser) {
        Start-Process $canvasUrl | Out-Null
    }
}

function Write-CompletionHints {
    Write-Host ""
    Write-Host "Manual acceptance" -ForegroundColor Cyan
    Write-Host "1. Cowork: upload the validated dist\cowork\agent-iq-canary.zip to the approved test tenant."
    Write-Host "2. Copilot: load packages\copilot with --plugin-dir and confirm agent-iq:agent-iq plus both skills."
    Write-Host "3. Canvas: use -CanvasFallback for browser/standalone replay; Desktop Present/fullscreen remains blocked."
    Write-Host "4. Live: label every result LIVE only after the requested certificate passes."
}

if ($OpenBrowser -and -not $CanvasFallback) {
    throw "-OpenBrowser requires -CanvasFallback."
}
if ($Mode -ne "LiveCanary" -and ($ProbeLive -or $ProbeCopilotHosts -or $PlanDeployment -or $DeployOrchestratorCanary)) {
    throw "Live probing, host probing, deployment planning, and deployment require -Mode LiveCanary."
}
if ($PlanDeployment -and $DeployOrchestratorCanary) {
    throw "Choose -PlanDeployment or -DeployOrchestratorCanary; do not combine them."
}
if ($SkipDeploymentBuild -and -not ($PlanDeployment -or $DeployOrchestratorCanary)) {
    throw "-SkipDeploymentBuild only applies to deployment planning or deployment."
}
if ($SkipDeploymentBuild -and [string]::IsNullOrWhiteSpace($ImageTag)) {
    throw "-SkipDeploymentBuild requires the exact existing -ImageTag."
}

. (Join-Path $PSScriptRoot "dev_env.ps1")
$uvPath = Get-ExecutablePath -Name "uv"

Push-Location $root
try {
    Write-Host "Agent IQ updated demo: $Mode" -ForegroundColor Green
    Write-PlaneBoundary
    Write-DemoPrompts
    $deploymentReportsBefore = @()
    if ($DeployOrchestratorCanary) {
        $deploymentReportsBefore = @(Get-DeploymentReportFiles)
    }

    switch ($Mode) {
        "Offline" {
            Invoke-LocalContracts -UvPath $uvPath -Offline
            Write-Host "Offline path: PASS (no Azure login, deployment, or live probe)." -ForegroundColor Green
        }
        "Preflight" {
            Invoke-CheckedCommand `
                -Description "Run the repository preflight gate without dependency sync" `
                -FilePath (Get-ExecutablePath -Name "pwsh") `
                -CommandArguments @("-NoProfile", "-File", $verifyScript, "-SkipSync")
            Write-Host "Preflight path: PASS." -ForegroundColor Green
        }
        "LiveCanary" {
            Invoke-LocalContracts -UvPath $uvPath
            if ($PlanDeployment -or $DeployOrchestratorCanary) {
                Invoke-CanaryDeployment -PwshPath (Get-ExecutablePath -Name "pwsh")
            }
            if ($ProbeCopilotHosts) {
                Invoke-CopilotHostCertificates -UvPath $uvPath
            }
            if ($ProbeLive) {
                $liveDeploymentReport = Resolve-ExplicitDeploymentReport `
                    -CandidatePath $DeploymentReportPath `
                    -PriorReports $deploymentReportsBefore `
                    -AfterDeployment:$DeployOrchestratorCanary
                Invoke-LiveCertificates `
                    -UvPath $uvPath `
                    -DeploymentReportPath $liveDeploymentReport
            }
            if (-not ($PlanDeployment -or $DeployOrchestratorCanary -or $ProbeCopilotHosts -or $ProbeLive)) {
                Write-Host "Live-canary path: prepared only; no Azure mutation or network probe was requested."
            }
        }
    }

    if ($CanvasFallback) {
        Start-CanvasFallback -NodePath (Get-ExecutablePath -Name "node.exe")
    }
    Write-CompletionHints
}
finally {
    Pop-Location
}
