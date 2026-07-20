param(
    [string]$McpUrl,
    [string]$Intent = "Which enzymes improve BrightWash cold-water cleaning?",
    [string]$ExpectedAgent = "consumer-formulation-rd",
    [string[]]$ExpectedSourceId = @(),
    [ValidateRange(1, 900)]
    [int]$TimeoutSeconds = 360,
    [switch]$Json,
    [switch]$NoReport
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "dev_env.ps1")

Push-Location $root
try {
    $arguments = @(
        "run",
        "python",
        "scripts/probe_cowork_connector.py",
        "--intent",
        $Intent,
        "--expected-agent",
        $ExpectedAgent,
        "--timeout-seconds",
        $TimeoutSeconds
    )
    if ($McpUrl) {
        $arguments += @("--mcp-url", $McpUrl)
    }
    foreach ($sourceId in $ExpectedSourceId) {
        $arguments += @("--expected-source-id", $sourceId)
    }
    if ($Json) {
        $arguments += "--json"
    }
    if ($NoReport) {
        $arguments += "--no-report"
    }
    & uv @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Agent IQ Cowork connector probe failed."
    }
}
finally {
    Pop-Location
}
