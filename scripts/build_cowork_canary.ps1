param(
    [switch]$Check
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "dev_env.ps1")

Push-Location $root
try {
    $arguments = @("run", "python", "deployments/cowork/build.py")
    if ($Check) {
        $arguments += "--check"
    }
    & uv @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Agent IQ Cowork canary package build failed."
    }
}
finally {
    Pop-Location
}
