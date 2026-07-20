param(
    [string]$CredentialFile = (
        Join-Path $env:USERPROFILE "foundry-azd\foundry.sp.env.ps1"
    ),
    [string]$SubscriptionId = $env:AZURE_SUBSCRIPTION_ID,
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"
foreach ($name in @(
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "AZURE_CLI_DISABLE_CONNECTION_VERIFICATION"
)) {
    Remove-Item "Env:\$name" -ErrorAction SilentlyContinue
}

$currentAzd = Join-Path $env:LOCALAPPDATA "Programs\Azure Dev CLI"
if (Test-Path $currentAzd) {
    $env:PATH = "$currentAzd;$env:PATH"
}

$useCurrentAzureSession = (
    $env:AGENT_IQ_USE_CURRENT_AZURE_SESSION -ceq "true"
)
if ((Test-Path $CredentialFile) -and -not $useCurrentAzureSession) {
    . $CredentialFile
    foreach ($name in @(
        "AZURE_CLIENT_ID",
        "AZURE_TENANT_ID",
        "AZURE_CLIENT_SECRET",
        "AZURE_SUBSCRIPTION_ID"
    )) {
        if (-not (Get-Item "Env:\$name" -ErrorAction SilentlyContinue)) {
            throw "$name was not set by $CredentialFile."
        }
    }

    & azd auth login --client-id $env:AZURE_CLIENT_ID `
        --tenant-id $env:AZURE_TENANT_ID `
        --client-secret $env:AZURE_CLIENT_SECRET | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "azd service-principal login failed."
    }

    & az login --service-principal `
        --username $env:AZURE_CLIENT_ID `
        --password $env:AZURE_CLIENT_SECRET `
        --tenant $env:AZURE_TENANT_ID `
        --only-show-errors | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Azure CLI service-principal login failed."
    }
}
else {
    $current = & az account show --output json --only-show-errors 2>$null |
        ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $null -eq $current) {
        throw (
            "Azure login is unavailable. Run 'az login' and 'azd auth login', " +
            "or provide a service-principal credential file."
        )
    }
    if ([string]::IsNullOrWhiteSpace($SubscriptionId)) {
        $SubscriptionId = [string]$current.id
    }
    $env:AZURE_SUBSCRIPTION_ID = $SubscriptionId
    $env:AZURE_TENANT_ID = [string]$current.tenantId
    if (
        $current.user.type -ceq "servicePrincipal" -and
        [string]::IsNullOrWhiteSpace($env:AZURE_CLIENT_ID)
    ) {
        $env:AZURE_CLIENT_ID = [string]$current.user.name
    }
    & azd auth login --check-status *> $null
    if ($LASTEXITCODE -ne 0) {
        throw (
            "Azure Developer CLI login is unavailable. Run 'azd auth login' " +
            "for the same tenant, then retry."
        )
    }
}

& az account set --subscription $env:AZURE_SUBSCRIPTION_ID --only-show-errors
if ($LASTEXITCODE -ne 0) {
    throw "Azure subscription selection failed."
}
$account = & az account show --query "{name:name,state:state,tenantId:tenantId,user:user}" `
    --output json --only-show-errors | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $null -eq $account) {
    throw "Azure subscription state is unavailable."
}
if ($account.state -cne "Enabled") {
    throw "Azure subscription '$($account.name)' is $($account.state); deployment requires Enabled."
}
$env:AZURE_TENANT_ID = [string]$account.tenantId

if (-not $Quiet) {
    Write-Host "Foundry login: $($account.user.name) / $($account.name)"
}
