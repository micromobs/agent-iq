param(
    [switch]$SkipSync,
    [switch]$ProbeCoworkConnector,
    [switch]$ProbeCopilotPlugin,
    [switch]$ProbeCopilotCanvas
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "dev_env.ps1")
$foundryScratch = Join-Path ([System.IO.Path]::GetTempPath()) "agent-iq-foundry-$PID"
$packageScratch = Join-Path ([System.IO.Path]::GetTempPath()) "agent-iq-packages-$PID"

function Assert-Step {
    param([string]$Name)

    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE."
    }
}

function Invoke-NpmCi {
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        $output = @(& npm ci --no-audit --no-fund 2>&1)
        $output | Out-Host
        $exitCode = $LASTEXITCODE
        if ($exitCode -eq 0) {
            return
        }
        $isBusy = ($output -join [Environment]::NewLine) -match "\bEBUSY\b"
        if (-not $isBusy -or $attempt -eq 3) {
            throw "Canvas dependency install failed with exit code $exitCode."
        }
        Start-Sleep -Seconds (2 * $attempt)
    }
}

function Test-NpmAuditTransportFailure {
    param([string]$Output)

    $transportSignature = @'
(?imx)
\b(?:ECONNREFUSED|ECONNRESET|ETIMEDOUT|ENOTFOUND|EAI_AGAIN|ERR_SSL_[A-Z_]+|
UNABLE_TO_GET_ISSUER|UNABLE_TO_VERIFY_LEAF_SIGNATURE|SELF_SIGNED_CERT_IN_CHAIN|
DEPTH_ZERO_SELF_SIGNED_CERT|CERT_HAS_EXPIRED)\b|
self[- ]signed certificate|
unable to (?:verify the )?(?:first )?certificate|
unable to get (?:local )?issuer certificate|
certificate verify failed|
request to https?://\S+ failed,\s*reason:
'@

    return [bool]($Output -match $transportSignature)
}

function Invoke-NpmAudit {
    $output = @(& npm audit --audit-level=low 2>&1)
    $exitCode = $LASTEXITCODE
    $output | Out-Host

    if ($exitCode -eq 0) {
        return
    }

    $auditOutput = $output -join [Environment]::NewLine
    if (Test-NpmAuditTransportFailure -Output $auditOutput) {
        Write-Warning (
            "Canvas dependency audit is externally blocked by a concrete registry transport failure " +
            "(exit code $exitCode). OSV remains the authoritative dependency vulnerability gate."
        )
        return
    }

    throw "Canvas dependency audit failed with exit code $exitCode; output was not a concrete registry transport failure."
}

Push-Location $root
try {
    if (-not $SkipSync) {
        & uv sync --all-groups --locked
        Assert-Step "Dependency sync"
    }

    & uv run python scripts/check_repo.py
    Assert-Step "Repository policy"

    $sourceFiles = @(git ls-files --cached --others --exclude-standard)
    if ($sourceFiles.Count -gt 0) {
        & uv run detect-secrets-hook --baseline .secrets.baseline @sourceFiles
        Assert-Step "Secret scan"
    }

    & uv run ruff format --check .
    Assert-Step "Format check"

    & uv run ruff check .
    Assert-Step "Lint"

    & uv run python scripts/generate_schemas.py --check
    Assert-Step "JSON Schema publication"

    & uv run python scripts/generate_repository_guide.py --check
    Assert-Step "Repository guide"

    & uv run python scripts/validate_consumer_assets.py
    Assert-Step "Consumer assets"

    $coworkScratch = Join-Path $packageScratch "cowork"
    & uv run python deployments/cowork/build.py --out $coworkScratch --json
    Assert-Step "Cowork canary package build"
    & uv run python deployments/cowork/build.py --out $coworkScratch --check --json
    Assert-Step "Cowork canary package verification"

    $copilotScratch = Join-Path $packageScratch "copilot"
    & uv run python deployments/copilot/build.py --out $copilotScratch --json
    Assert-Step "Copilot plugin package build"
    & uv run python deployments/copilot/build.py --out $copilotScratch --check --json
    Assert-Step "Copilot plugin package verification"

    & uv run python scripts/generate_canvas_demo.py --check
    Assert-Step "Canvas replay"

    Push-Location (Join-Path $root "apps\canvas")
    try {
        Invoke-NpmCi

        Invoke-NpmAudit

        & npm run typecheck
        Assert-Step "Canvas type check"

        & npm test
        Assert-Step "Canvas tests"

        & npm run build:check
        Assert-Step "Canvas deterministic build"
    }
    finally {
        Pop-Location
    }

    & osv-scanner scan --lockfile uv.lock --lockfile apps/canvas/package-lock.json
    Assert-Step "Dependency vulnerability scan (OSV)"

    & node --check .github/extensions/agent-iq-canvas/extension.mjs
    Assert-Step "Canvas extension syntax"

    & node --check .github/extensions/agent-iq-canvas/runtime.mjs
    Assert-Step "Canvas runtime syntax"

    if ($ProbeCopilotPlugin) {
        & uv run python scripts/probe_copilot_plugin.py --no-report --json
        Assert-Step "GitHub Copilot plugin host"
    }

    if ($ProbeCopilotCanvas) {
        & uv run python scripts/probe_copilot_canvas.py --no-report --json
        Assert-Step "GitHub Copilot Canvas host"
    }

    if ($ProbeCoworkConnector) {
        & uv run python scripts/probe_cowork_connector.py --no-report --json
        Assert-Step "Cowork remote MCP connector"
    }

    & uv run python deployments/foundry/consumer/generate.py --all --out $foundryScratch
    Assert-Step "Foundry deployment generation"

    & uv run python deployments/foundry/consumer/validate.py --all --out $foundryScratch
    Assert-Step "Foundry deployment validation"

    & uv run python scripts/run_discovery_eval.py --semantic offline --json
    Assert-Step "Offline discovery quality"

    & uv run python scripts/run_orchestration_eval.py --semantic offline --json
    Assert-Step "Offline orchestration quality"

    & uv run python scripts/run_tool_call_eval.py --json
    Assert-Step "Offline hero tool-call quality"

    & uv run python scripts/run_rubric_eval.py --json
    Assert-Step "Offline release rubric"

    & uv run mypy src tests scripts deployments
    Assert-Step "Type check"

    & uv run pytest --cov=agent_iq --cov-report=term-missing --cov-fail-under=90
    Assert-Step "Tests"

    & uv build
    Assert-Step "Package build"

    $distributions = @(
        Get-ChildItem (Join-Path $root "dist\*") -File |
            Where-Object { $_.Name -match "\.(?:whl|tar\.gz)$" }
    )
    if ($distributions.Count -eq 0) {
        throw "Package build produced no publishable distributions."
    }
    & uv run python scripts/check_distribution.py @($distributions.FullName)
    Assert-Step "Distribution policy"

    $wheel = Get-ChildItem (Join-Path $root "dist\*.whl") |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    & uv run python scripts/check_wheel.py $wheel.FullName
    Assert-Step "Wheel assets"

    Write-Host "Agent IQ verification: PASS"
}
finally {
    Pop-Location
    if (Test-Path $foundryScratch) {
        Remove-Item -Recurse -Force $foundryScratch
    }
    if (Test-Path $packageScratch) {
        Remove-Item -Recurse -Force $packageScratch
    }
}
