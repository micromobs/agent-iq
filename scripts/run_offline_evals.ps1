param(
    [switch]$Open
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "dev_env.ps1")

function Invoke-Eval {
    param(
        [string]$Script,
        [string[]]$Arguments = @()
    )

    & uv run python $Script @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Script failed with exit code $LASTEXITCODE."
    }
}

Push-Location $root
try {
    $discoveryArgs = @("--semantic", "offline")
    if ($Open) {
        $discoveryArgs += "--open-report"
    }
    Invoke-Eval "scripts/run_discovery_eval.py" $discoveryArgs
    Invoke-Eval "scripts/run_orchestration_eval.py" @("--semantic", "offline")
    Invoke-Eval "scripts/run_tool_call_eval.py"
    Invoke-Eval "scripts/run_rubric_eval.py"
    Invoke-Eval "scripts/run_foundry_native_eval.py" @("--validate-only")

    Write-Host "Eval Center: $(Join-Path $root 'reports\evals\discovery\latest.html')"
    Write-Host "Orchestration: $(Join-Path $root 'reports\evals\orchestration\latest.json')"
    Write-Host "Hero tools: $(Join-Path $root 'reports\evals\tool-calls\latest.json')"
    Write-Host "Release rubric: $(Join-Path $root 'reports\evals\rubrics\latest.html')"
    Write-Host "Native Foundry suite: validated (live certification is explicit)"
}
finally {
    Pop-Location
}
