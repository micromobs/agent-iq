"""Static and offline safety checks for the Windows updated-demo launcher."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = REPOSITORY_ROOT / "scripts" / "run_updated_demo.ps1"
SCENARIO = REPOSITORY_ROOT / "evals" / "launch_command_center.json"
CERTIFIER = REPOSITORY_ROOT / "scripts" / "certify_launch_command_center.py"
DEPLOYER = REPOSITORY_ROOT / "scripts" / "deploy_orchestrator_canary.ps1"
POWERSHELL = shutil.which("pwsh")
AZURE_AI_ACCOUNT = "ai-updated-demo-test"
FOUNDRY_PROJECT = "demo"
FOUNDRY_PROJECT_ENDPOINT = (
    f"https://{AZURE_AI_ACCOUNT}.services.ai.azure.com/api/projects/{FOUNDRY_PROJECT}"
)


def _run_powershell(
    *arguments: str,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    if POWERSHELL is None:
        pytest.skip("PowerShell 7 is required for the Windows launcher test.")
    child_environment = os.environ.copy()
    if environment:
        child_environment.update(environment)
    return subprocess.run(
        [POWERSHELL, "-NoProfile", *arguments],
        cwd=REPOSITORY_ROOT,
        env=child_environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _assert_powershell_parseable(path: Path) -> None:
    parse_command = r"""
$tokens = $null
$errors = $null
[System.Management.Automation.Language.Parser]::ParseFile(
    $env:PARSE_TARGET,
    [ref]$tokens,
    [ref]$errors
) | Out-Null
if ($errors.Count -gt 0) {
    $errors | ForEach-Object { Write-Error $_.Message }
    exit 1
}
"""
    result = _run_powershell(
        "-Command",
        parse_command,
        environment={"PARSE_TARGET": str(path)},
    )
    assert result.returncode == 0, result.stderr


def _run_deployer(*arguments: str) -> subprocess.CompletedProcess[str]:
    supplied = {argument.casefold() for argument in arguments if argument.startswith("-")}
    endpoint_arguments: list[str] = []
    if "-foundryproject" not in supplied:
        endpoint_arguments.extend(["-FoundryProject", FOUNDRY_PROJECT])
    if "-foundryprojectendpoint" not in supplied:
        endpoint_arguments.extend(["-FoundryProjectEndpoint", FOUNDRY_PROJECT_ENDPOINT])
    if "-azureopenaiendpoint" not in supplied:
        endpoint_arguments.extend(
            [
                "-AzureOpenAiEndpoint",
                "https://ai-updated-demo-test.cognitiveservices.azure.com/",
            ]
        )
    return _run_powershell(
        "-File",
        str(DEPLOYER),
        "-ResourceGroup",
        "rg-updated-demo-test",
        "-ContainerAppsEnvironment",
        "env-updated-demo-test",
        "-AcrName",
        "acrupdateddemotest",
        "-AiAccount",
        AZURE_AI_ACCOUNT,
        "-SearchService",
        "search-updated-demo-test",
        *endpoint_arguments,
        *arguments,
    )


def test_launcher_is_parseable_and_defaults_closed() -> None:
    _assert_powershell_parseable(LAUNCHER)

    source = LAUNCHER.read_text(encoding="utf-8")
    assert '[string]$Mode = "Offline"' in source
    assert "Set-StrictMode -Version Latest" in source
    assert '[Alias("Deploy")]' in source
    assert "-DeployOrchestratorCanary" in source
    assert '"-WhatIf"' in source
    assert "-Delete" not in source
    assert "[string]$AppInsightsName" in source
    assert "[string]$FoundryProject" in source
    assert '"-FoundryProject", $FoundryProject' in source
    assert '"-AppInsightsName", $AppInsightsName' in source
    assert '"-ImageTag", $ImageTag' in source
    assert "[string]$DeploymentReportPath" in source
    assert "orchestrator-canary-run-\\d{8}T\\d{13}Z-" in source
    assert "function Resolve-ExplicitDeploymentReport" in source
    assert '"--deployment-report", $DeploymentReportPath' in source
    assert "latest.json" in source
    assert '"run", "build"' in source
    assert '"run", "build:check"' in source
    assert '"--offline", "--no-sync"' in source
    assert "Invoke-LocalContracts -UvPath $uvPath -Offline" in source
    assert "Start-CanvasFallback -NodePath" in source
    assert "node.exe" in source
    assert "node_modules\\vite\\bin\\vite.js" in source
    assert "--strictPort" in source
    assert "launch-command-center.agui.json" in source
    assert "demo-eu-launch-command-center" in source
    assert "Stop-Process -Id $process.Id" in source
    assert "-FilePath $NpmPath" not in source
    assert '"run", "dev"' not in source
    assert "--no-report" not in source
    assert source.index("scripts\\certify_orchestrator_canary.py") < source.index(
        "scripts\\certify_launch_command_center.py"
    )
    assert "$Args" not in source

    scenario = json.loads(SCENARIO.read_text(encoding="utf-8"))
    assert scenario["schemaVersion"] == "1.0"
    assert len(scenario["rehearsalPrompts"]) == 5
    assert "launch_command_center.json" in source
    assert "launch_command_center.json" in CERTIFIER.read_text(encoding="utf-8")
    assert scenario["intent"] not in source
    assert "d12-command-center-" in DEPLOYER.read_text(encoding="utf-8")


def test_deployer_is_parseable_and_pins_a_verified_digest_before_mutation() -> None:
    _assert_powershell_parseable(DEPLOYER)

    source = DEPLOYER.read_text(encoding="utf-8")
    assert "Set-StrictMode -Version Latest" in source
    assert '$canaryNamePattern = "^agent-iq-orchestrator-canary(?:-[a-z0-9]+)?$"' in source
    assert "function Assert-CanaryAppName" in source
    assert "function Assert-CanaryIdentity" in source
    assert "function Assert-CanaryMutationAllowed" in source
    assert "function Get-TagNames" in source
    assert "ownership tag keys must" in source
    assert '"agent-iq-owner" = "agent-iq"' in source
    assert '"agent-iq-component" = "orchestrator"' in source
    assert '"agent-iq-canary" = "true"' in source
    assert '"agent-iq-environment" = "canary"' in source
    assert '"agent-iq-purpose" = "canary"' in source
    assert '"agent-iq-managed-by" = "deploy_orchestrator_canary"' in source
    assert "--tags $canaryTagArguments" in source
    assert "AGENT_IQ_ALLOW_ANONYMOUS_CONNECTOR=true" in source

    create_call = source.index("& az containerapp create")
    assert source.rfind("Assert-CanaryMutationAllowed -ForCreate", 0, create_call) > 0
    delete_call = source.index("& az containerapp delete")
    assert source.rfind("Assert-CanaryMutationAllowed", 0, delete_call) > 0
    for mutation in (
        "& az containerapp registry set",
        "& az containerapp secret set",
        "& az containerapp update",
        "& az containerapp ingress update",
    ):
        mutation_call = source.index(mutation)
        assert source.rfind("$null = Assert-CanaryMutationAllowed", 0, mutation_call) > 0

    traffic_start = source.index("function Set-CanaryTrafficTarget")
    rollback_start = source.index("function Restore-CanaryTraffic")
    report_start = source.index("function New-DeploymentReportPath")
    traffic_source = source[traffic_start:rollback_start]
    rollback_source = source[rollback_start:report_start]
    assert "latestReadyRevisionName" in source
    assert "function Set-CanaryMultipleRevisionMode" in source
    assert "function Assert-CanaryTrafficTarget" in source
    assert "function Confirm-CanaryRevisionImage" in source
    assert "function Confirm-CanaryContract" in source
    assert "AGENT_IQ_DEPLOYMENT_PROBE_BASE_URL=$candidateBaseUrl" in source
    assert "--revisions-mode multiple" in source
    assert "--revision-suffix $candidateRevisionSuffix" in source
    assert "candidate_probe_then_atomic_traffic" in source
    assert "candidateValidatedBeforeTraffic = $true" in source
    assert "rollbackReady = $rollbackReady" in source
    assert "revision activate" in rollback_source
    assert "Set-CanaryTrafficTarget -RevisionName $revisionName" in rollback_source
    assert "ingress traffic set" in traffic_source
    assert "--revision-weight" in traffic_source
    assert "containerapp delete" not in rollback_source
    assert "Original error:" in source
    assert "Rollback error:" in source

    deployment_start = source.index("$candidateCreated = $false")
    deployment_source = source[deployment_start:]
    mode_call = deployment_source.index("$existing = Set-CanaryMultipleRevisionMode")
    pin_call = deployment_source.index(
        "$null = Set-CanaryTrafficTarget -RevisionName $stagingRevision"
    )
    update_call = deployment_source.index("& az containerapp update")
    image_check = deployment_source.index("$null = Confirm-CanaryRevisionImage")
    candidate_probe = deployment_source.index(
        "$ready = Confirm-CanaryContract `\n        -BaseUrl $candidateBaseUrl"
    )
    promote_call = deployment_source.index(
        "$null = Set-CanaryTrafficTarget -RevisionName $candidateRevisionName"
    )
    public_probe = deployment_source.index(
        "$ready = Confirm-CanaryContract `\n        -BaseUrl $publicBaseUrl"
    )
    deactivate_previous = deployment_source.index(
        "Stop-CanaryRevision -RevisionName $stagingRevision"
    )
    assert mode_call < pin_call < update_call < image_check
    assert image_check < candidate_probe < promote_call < public_probe
    assert public_probe < deactivate_previous
    assert "rollbackActivationRequired = $(" in deployment_source

    assert "function Write-AtomicJsonFile" in source
    assert "[System.IO.File]::Move" in source
    assert "[Guid]::NewGuid().ToString" in source
    assert "function Remove-OldDeploymentReports" in source
    assert '$reportRunPrefix = "orchestrator-canary-run-"' in source
    assert "$maxDeploymentReports = 20" in source
    assert "-cmatch $runPattern" in source
    assert '$reportPath = Join-Path $reportRoot "$ImageTag.json"' not in source
    assert "Set-Content" not in source

    assert "function Resolve-ImageDigest" in source
    assert "az acr manifest show-metadata" in source
    assert "sha256:[0-9a-fA-F]{64}" in source
    assert "The requested ACR image tag" in source
    assert "malformed sha256 digest" in source
    assert '$immutableImage = "$loginServer/$Repository@$imageDigest"' in source
    assert "--image $immutableImage" in source
    assert "imageTag = $ImageTag" in source
    assert "imageTagReference = $image" in source
    assert "imageDigest = $imageDigest" in source
    assert "quickstart:latest" not in source
    resolve_call = source.index("$imageDigest = Resolve-ImageDigest")
    assert resolve_call < source.index("az containerapp create")
    assert resolve_call < source.index("az containerapp update")
    assert resolve_call < source.index("az role assignment create")
    assert source.count("--image $immutableImage") == 2
    assert "--image $image" not in source
    assert "function ConvertTo-SafeAzureEndpoint" in source
    assert "function ConvertTo-CanonicalAzureAiAccount" in source
    assert "function ConvertTo-CanonicalAzureAiProject" in source
    assert "function Resolve-BoundFoundryProjectEndpoint" in source
    assert "function Resolve-BoundAzureOpenAiEndpoint" in source
    assert "/api/projects/<project-name>" in source
    account_validation = source.index("$AiAccount = ConvertTo-CanonicalAzureAiAccount")
    project_validation = source.index("$FoundryProject = ConvertTo-CanonicalAzureAiProject")
    foundry_binding = source.index("$FoundryProjectEndpoint = Resolve-BoundFoundryProjectEndpoint")
    endpoint_validation = source.index("$AzureOpenAiEndpoint = Resolve-BoundAzureOpenAiEndpoint")
    assert account_validation < project_validation < foundry_binding < endpoint_validation
    assert foundry_binding < source.index("if ($WhatIf)")
    assert foundry_binding < source.index(". $loginScript -Quiet")
    assert endpoint_validation < source.index("if ($WhatIf)")
    assert endpoint_validation < source.index(". $loginScript -Quiet")
    assert '"AGENT_IQ_AZURE_AI_ACCOUNT=$AiAccount"' in source
    assert '"AGENT_IQ_AZURE_AI_PROJECT=$FoundryProject"' in source


def test_deployer_plan_rejects_non_canary_names_without_login_or_mutation() -> None:
    result = _run_deployer("-WhatIf", "-AppName", "agent-iq-orchestrator")

    assert result.returncode != 0
    assert "canary policy" in result.stderr
    assert "foundry_login.ps1" not in result.stdout
    assert "az containerapp" not in result.stdout


def test_deployer_delete_rejects_non_canary_names_before_login() -> None:
    result = _run_deployer("-Delete", "-AppName", "unrelated-container-app")

    assert result.returncode != 0
    assert "canary policy" in result.stderr
    assert "foundry_login.ps1" not in result.stdout


def test_deployer_plan_requires_explicit_project_identity_before_login() -> None:
    result = _run_deployer("-WhatIf", "-FoundryProject", "")

    assert result.returncode != 0
    assert "Azure AI project must be a canonical resource name" in result.stderr
    assert "foundry_login.ps1" not in result.stdout
    assert "az containerapp" not in result.stdout


@pytest.mark.parametrize(
    ("argument", "unsafe_value"),
    (
        (
            "-FoundryProjectEndpoint",
            f"http://{AZURE_AI_ACCOUNT}.services.ai.azure.com/api/projects/{FOUNDRY_PROJECT}",
        ),
        (
            "-FoundryProjectEndpoint",
            f"https://user@{AZURE_AI_ACCOUNT}.services.ai.azure.com/api/projects/{FOUNDRY_PROJECT}",
        ),
        (
            "-FoundryProjectEndpoint",
            f"https://{AZURE_AI_ACCOUNT}.services.ai.azure.com:443/api/projects/{FOUNDRY_PROJECT}",
        ),
        (
            "-FoundryProjectEndpoint",
            "https://other-account.services.ai.azure.com/api/projects/demo",
        ),
        (
            "-FoundryProjectEndpoint",
            f"https://{AZURE_AI_ACCOUNT}.services.ai.azure.com/api/projects/other-project",
        ),
        (
            "-FoundryProjectEndpoint",
            f"https://{AZURE_AI_ACCOUNT}.services.ai.azure.com/api/projects/{FOUNDRY_PROJECT}/extra",
        ),
        (
            "-FoundryProjectEndpoint",
            f"{FOUNDRY_PROJECT_ENDPOINT}?api-version=v1",
        ),
        (
            "-FoundryProjectEndpoint",
            f"{FOUNDRY_PROJECT_ENDPOINT}#fragment",
        ),
        (
            "-FoundryProjectEndpoint",
            (f"https://{AZURE_AI_ACCOUNT}%2eservices.ai.azure.com/api/projects/{FOUNDRY_PROJECT}"),
        ),
        (
            "-FoundryProjectEndpoint",
            (f"https://{AZURE_AI_ACCOUNT}.services.ai.azure.com/api/projects/%2e%2e"),
        ),
        (
            "-FoundryProjectEndpoint",
            FOUNDRY_PROJECT_ENDPOINT.upper(),
        ),
        (
            "-FoundryProjectEndpoint",
            f"{FOUNDRY_PROJECT_ENDPOINT}/",
        ),
        ("-AzureOpenAiEndpoint", "https://example.invalid/"),
        (
            "-AzureOpenAiEndpoint",
            "https://attacker.cognitiveservices.azure.com/",
        ),
        (
            "-AzureOpenAiEndpoint",
            "https://ai-updated-demo-test.cognitiveservices.azure.com:443/",
        ),
        (
            "-AzureOpenAiEndpoint",
            "https://user@ai-updated-demo-test.cognitiveservices.azure.com/",
        ),
        (
            "-AzureOpenAiEndpoint",
            "https://ai-updated-demo-test%2ecognitiveservices.azure.com/",
        ),
        (
            "-AzureOpenAiEndpoint",
            "https://ai-updated-demo-test.cognitiveservices.azure.com/?api-version=v1",
        ),
        (
            "-AzureOpenAiEndpoint",
            "https://ai-updated-demo-test.cognitiveservices.azure.com/#fragment",
        ),
        (
            "-AzureOpenAiEndpoint",
            "https://ai-updated-demo-test.cognitiveservices.azure.com/openai",
        ),
    ),
)
def test_deployer_plan_rejects_unsafe_endpoints_before_login(
    argument: str,
    unsafe_value: str,
) -> None:
    result = _run_deployer("-WhatIf", argument, unsafe_value)

    assert result.returncode != 0
    assert "foundry_login.ps1" not in result.stdout
    assert "az containerapp" not in result.stdout
    if argument == "-AzureOpenAiEndpoint":
        assert "Azure OpenAI endpoint" in result.stderr


def test_deployer_rejects_environment_project_mismatch_before_any_cloud_boundary(
    tmp_path: Path,
) -> None:
    command = r"""
$global:CloudCalls = 0
function global:az { $global:CloudCalls++; throw "AZ_SENTINEL" }
function global:azd { $global:CloudCalls++; throw "AZD_SENTINEL" }
function global:docker { $global:CloudCalls++; throw "DOCKER_SENTINEL" }
function global:Invoke-RestMethod { $global:CloudCalls++; throw "REST_SENTINEL" }
function global:Invoke-WebRequest { $global:CloudCalls++; throw "WEB_SENTINEL" }
try {
    & $env:DEPLOYER_PATH `
        -ResourceGroup rg-updated-demo-test `
        -ContainerAppsEnvironment env-updated-demo-test `
        -AcrName acrupdateddemotest `
        -AiAccount ai-updated-demo-test `
        -FoundryProject demo `
        -SearchService search-updated-demo-test
    throw "Mismatched environment endpoint was accepted."
}
catch {
    if ($_.Exception.Message -notmatch "must exactly match") {
        throw
    }
}
if ($global:CloudCalls -ne 0) {
    throw "Endpoint mismatch reached $global:CloudCalls cloud boundary call(s)."
}
exit 0
"""
    result = _run_powershell(
        "-Command",
        command,
        environment={
            "DEPLOYER_PATH": str(DEPLOYER),
            "AGENT_IQ_FOUNDRY_PROJECT_ENDPOINT": (
                "https://other-account.services.ai.azure.com/api/projects/demo"
            ),
            "USERPROFILE": str(tmp_path),
        },
    )

    assert result.returncode == 0, result.stderr


def test_deployer_plan_derives_identity_bound_endpoints_without_login_or_mutation() -> None:
    result = _run_powershell(
        "-File",
        str(DEPLOYER),
        "-ResourceGroup",
        "rg-updated-demo-test",
        "-ContainerAppsEnvironment",
        "env-updated-demo-test",
        "-AcrName",
        "acrupdateddemotest",
        "-AiAccount",
        AZURE_AI_ACCOUNT,
        "-FoundryProject",
        FOUNDRY_PROJECT,
        "-SearchService",
        "search-updated-demo-test",
        "-WhatIf",
    )

    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["azureAiAccount"] == AZURE_AI_ACCOUNT
    assert plan["azureAiProject"] == FOUNDRY_PROJECT
    assert plan["projectEndpoint"] == FOUNDRY_PROJECT_ENDPOINT
    assert (
        plan["azureOpenAiEndpoint"] == "https://ai-updated-demo-test.cognitiveservices.azure.com/"
    )
    assert "foundry_login.ps1" not in result.stdout
    assert "az containerapp" not in result.stdout


def test_deployer_identity_and_rollback_helpers_are_mockable() -> None:
    command = r"""
$ErrorActionPreference = "Stop"
. $env:DEPLOYER_PATH `
    -ResourceGroup rg-updated-demo-test `
    -ContainerAppsEnvironment env-updated-demo-test `
    -AcrName acrupdateddemotest `
    -AiAccount ai-updated-demo-test `
    -FoundryProject demo `
    -SearchService search-updated-demo-test `
    -FoundryProjectEndpoint https://ai-updated-demo-test.services.ai.azure.com/api/projects/demo `
    -AzureOpenAiEndpoint https://ai-updated-demo-test.cognitiveservices.azure.com/ `
    -WhatIf `
    -ImageTag d12-test

$valid = [pscustomobject]@{
    name = "agent-iq-orchestrator-canary"
    tags = [pscustomobject]@{
        "agent-iq-owner" = "agent-iq"
        "agent-iq-component" = "orchestrator"
        "agent-iq-canary" = "true"
        "agent-iq-environment" = "canary"
        "agent-iq-purpose" = "canary"
        "agent-iq-managed-by" = "deploy_orchestrator_canary"
    }
}
Assert-CanaryIdentity -Resource $valid | Out-Null
$rejected = $false
try {
    Assert-CanaryIdentity -Resource (
        [pscustomobject]@{
            name = $valid.name
            tags = [pscustomobject]@{ "agent-iq-owner" = "agent-iq" }
        }
    ) | Out-Null
}
catch {
    $rejected = $true
}
if (-not $rejected) {
    throw "An untagged canary resource was accepted."
}
$extraTags = [ordered]@{}
foreach ($property in $valid.tags.PSObject.Properties) {
    $extraTags[$property.Name] = $property.Value
}
$extraTags["unexpected-owner-marker"] = "must-fail-closed"
$rejectedExtraTag = $false
try {
    Assert-CanaryIdentity -Resource (
        [pscustomobject]@{
            name = $valid.name
            tags = $extraTags
        }
    ) | Out-Null
}
catch {
    $rejectedExtraTag = $true
}
if (-not $rejectedExtraTag) {
    throw "A canary resource with an extra ownership tag was accepted."
}

$script:mockCalls = [System.Collections.Generic.List[string]]::new()
function New-MockCanaryResourceJson {
    param([switch]$WithExtraTag)
    $tags = [ordered]@{
        "agent-iq-owner" = "agent-iq"
        "agent-iq-component" = "orchestrator"
        "agent-iq-canary" = "true"
        "agent-iq-environment" = "canary"
        "agent-iq-purpose" = "canary"
        "agent-iq-managed-by" = "deploy_orchestrator_canary"
    }
    if ($WithExtraTag) {
        $tags["unexpected-tag"] = "reject"
    }
    return [pscustomobject]@{
        name = "agent-iq-orchestrator-canary"
        tags = $tags
        properties = [pscustomobject]@{
            configuration = [pscustomobject]@{
                activeRevisionsMode = "Multiple"
                ingress = [pscustomobject]@{
                    traffic = @(
                        [pscustomobject]@{
                            revisionName = "agent-iq-orchestrator-canary--old"
                            weight = 100
                        }
                    )
                }
            }
            latestReadyRevisionName = "agent-iq-orchestrator-canary--old"
        }
    } | ConvertTo-Json -Depth 8 -Compress
}
$script:showResourceJson = New-MockCanaryResourceJson
$script:revisionActive = $true
function az {
    param(
        [Parameter(Position = 0)]
        [string]$Command,
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Remaining
    )
    $all = @($Command) + @($Remaining)
    $script:mockCalls.Add(($all -join " "))
    if ($all.Count -ge 2 -and $all[0] -eq "containerapp" -and $all[1] -eq "show") {
        $script:showResourceJson
    }
    if (
        $all.Count -ge 3 -and
        $all[0] -eq "containerapp" -and
        $all[1] -eq "revision" -and
        $all[2] -eq "show"
    ) {
        [pscustomobject]@{
            name = "agent-iq-orchestrator-canary--old"
            properties = [pscustomobject]@{
                active = $script:revisionActive
            }
        } | ConvertTo-Json -Depth 8 -Compress
    }
    $global:LASTEXITCODE = 0
}
$rollbackState = [pscustomobject]@{
    available = $true
    revisionName = "agent-iq-orchestrator-canary--old"
    revisionMode = "Multiple"
    revisionWeights = @("agent-iq-orchestrator-canary--old=100")
    labelWeights = @()
}
$script:showResourceJson = New-MockCanaryResourceJson -WithExtraTag
$rollbackRejected = $false
try {
    Restore-CanaryTraffic -RollbackState $rollbackState | Out-Null
}
catch {
    $rollbackRejected = $true
}
if (-not $rollbackRejected) {
    throw "Rollback accepted a canary resource with an extra ownership tag."
}
if (($script:mockCalls -join "`n") -match "revision activate|ingress traffic set") {
    throw "Rollback mutated a canary resource with an extra ownership tag."
}
$script:showResourceJson = New-MockCanaryResourceJson
$script:mockCalls.Clear()
$script:revisionActive = $false
$rollbackResult = Restore-CanaryTraffic -RollbackState ([pscustomobject]@{
    available = $rollbackState.available
    revisionName = $rollbackState.revisionName
    revisionMode = $rollbackState.revisionMode
    revisionWeights = $rollbackState.revisionWeights
    labelWeights = $rollbackState.labelWeights
})
if (($script:mockCalls -join "`n") -notmatch "containerapp revision activate") {
    throw "Rollback did not activate the previous revision."
}
if (($script:mockCalls -join "`n") -notmatch "containerapp ingress traffic set") {
    throw "Rollback did not restore traffic."
}
if (($script:mockCalls -join "`n") -match "containerapp delete") {
    throw "Rollback attempted destructive deletion."
}
$script:mockCalls.Clear()
$script:revisionActive = $true
$activeRollbackResult = Restore-CanaryTraffic -RollbackState $rollbackState
if (($script:mockCalls -join "`n") -match "containerapp revision activate") {
    throw "Rollback tried to activate an already-active revision."
}
if (($script:mockCalls -join "`n") -notmatch "containerapp ingress traffic set") {
    throw "Rollback did not restore traffic to the already-active revision."
}
if ($activeRollbackResult -notmatch "100 percent") {
    throw "Active-revision rollback did not report exact traffic restoration."
}
$latestResource = New-MockCanaryResourceJson | ConvertFrom-Json
$latestResource.properties.configuration.activeRevisionsMode = "Single"
$latestResource.properties.configuration.ingress.traffic = @(
    [pscustomobject]@{
        latestRevision = $true
        weight = 100
    }
)
$latestState = Get-CanaryRollbackState -Resource $latestResource
if ($latestState.revisionName -ne "agent-iq-orchestrator-canary--old") {
    throw "Latest-revision traffic did not resolve to its immutable revision."
}
$splitResource = New-MockCanaryResourceJson | ConvertFrom-Json
$splitResource.properties.configuration.ingress.traffic = @(
    [pscustomobject]@{
        revisionName = "agent-iq-orchestrator-canary--old"
        weight = 50
    },
    [pscustomobject]@{
        revisionName = "agent-iq-orchestrator-canary--new"
        weight = 50
    }
)
$splitRejected = $false
try {
    Get-CanaryRollbackState -Resource $splitResource | Out-Null
}
catch {
    $splitRejected = $true
}
if (-not $splitRejected) {
    throw "Process-local canary accepted split revision traffic."
}
$script:showResourceJson = $latestResource | ConvertTo-Json -Depth 8 -Compress
$script:mockCalls.Clear()
$singleModeRejected = $false
try {
    Restore-CanaryTraffic -RollbackState $rollbackState | Out-Null
}
catch {
    $singleModeRejected = $true
}
if (-not $singleModeRejected) {
    throw "Rollback falsely succeeded while the canary remained in single-revision mode."
}
if (($script:mockCalls -join "`n") -match "revision activate|ingress traffic set") {
    throw "Single-revision rollback attempted a traffic mutation."
}
Write-Output $rollbackResult
Write-Output "mocked identity and rollback: PASS"
"""
    result = _run_powershell(
        "-Command",
        command,
        environment={"DEPLOYER_PATH": str(DEPLOYER)},
    )

    assert result.returncode == 0, result.stderr
    assert "mocked identity and rollback: PASS" in result.stdout
    assert "Rollback restored revision" in result.stdout


def test_offline_launcher_succeeds_without_azure_credentials_or_deployment() -> None:
    blocked_keys = (
        "AZURE_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "AGENT_IQ_AZURE_RESOURCE_GROUP",
        "AGENT_IQ_AZURE_CONTAINER_APPS_ENVIRONMENT",
        "AGENT_IQ_AZURE_CONTAINER_REGISTRY",
        "AGENT_IQ_AZURE_AI_ACCOUNT",
        "AGENT_IQ_AZURE_AI_PROJECT",
        "AGENT_IQ_SEARCH_SERVICE",
        "AGENT_IQ_FOUNDRY_PROJECT_ENDPOINT",
        "AGENT_IQ_AZURE_OPENAI_ENDPOINT",
        "AGENT_IQ_AZURE_APP_INSIGHTS",
        "AGENT_IQ_ORCHESTRATOR_BASE_URL",
    )
    environment = {key: "" for key in blocked_keys}
    result = _run_powershell(
        "-File",
        str(LAUNCHER),
        "-Mode",
        "Offline",
        environment=environment,
    )

    assert result.returncode == 0, result.stderr
    assert "Offline path: PASS (no Azure login, deployment, or live probe)." in result.stdout
    assert "Deploy the explicitly requested orchestrator canary" not in result.stdout
    assert "deploy_orchestrator_canary.ps1" not in result.stdout


def test_live_plan_is_non_mutating_and_forwards_what_if() -> None:
    coordinates = {
        "AGENT_IQ_AZURE_RESOURCE_GROUP": "rg-updated-demo-test",
        "AGENT_IQ_AZURE_CONTAINER_APPS_ENVIRONMENT": "env-updated-demo-test",
        "AGENT_IQ_AZURE_CONTAINER_REGISTRY": "acrupdateddemotest",
        "AGENT_IQ_AZURE_AI_ACCOUNT": AZURE_AI_ACCOUNT,
        "AGENT_IQ_AZURE_AI_PROJECT": FOUNDRY_PROJECT,
        "AGENT_IQ_SEARCH_SERVICE": "search-updated-demo-test",
        "AGENT_IQ_FOUNDRY_PROJECT_ENDPOINT": FOUNDRY_PROJECT_ENDPOINT,
        "AGENT_IQ_AZURE_OPENAI_ENDPOINT": (
            "https://ai-updated-demo-test.cognitiveservices.azure.com/"
        ),
        "AZURE_API_KEY": "",
    }
    result = _run_powershell(
        "-File",
        str(LAUNCHER),
        "-Mode",
        "LiveCanary",
        "-PlanDeployment",
        environment=coordinates,
    )

    assert result.returncode == 0, result.stderr
    assert '"anonymousCanary": true' in result.stdout
    assert '"imageTag": "d12-command-center-' in result.stdout
    assert "Deploy the explicitly requested orchestrator canary" not in result.stdout


def test_skip_build_requires_an_explicit_existing_image_tag() -> None:
    result = _run_powershell(
        "-File",
        str(LAUNCHER),
        "-Mode",
        "LiveCanary",
        "-PlanDeployment",
        "-SkipDeploymentBuild",
    )

    assert result.returncode != 0
    assert "requires the exact existing -ImageTag" in result.stderr
