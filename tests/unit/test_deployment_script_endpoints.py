"""Static and offline endpoint-safety tests for Foundry deployment scripts."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = (
    REPOSITORY_ROOT / "scripts" / "deploy_consumer_fleet.ps1",
    REPOSITORY_ROOT / "scripts" / "summarize_consumer_fleet.ps1",
    REPOSITORY_ROOT / "scripts" / "deploy_foundry_host_canary.ps1",
)
HOST_CANARY = SCRIPTS[2]
CONSUMER_DEPLOYER = SCRIPTS[0]
FOUNDRY_LOGIN = REPOSITORY_ROOT / "scripts" / "foundry_login.ps1"
EVIDENCE_CERTIFIER = REPOSITORY_ROOT / "scripts" / "certify_foundry_evidence.ps1"
ORCHESTRATION_CERTIFIER = REPOSITORY_ROOT / "scripts" / "certify_foundry_orchestration.ps1"
DEPLOYERS = (CONSUMER_DEPLOYER, HOST_CANARY)
POWERSHELL = shutil.which("pwsh")
VALID_ENDPOINT = "https://example-resource.services.ai.azure.com/api/projects/example"
BOUND_ACCOUNT = "account-test"
BOUND_PROJECT = "project-test"
BOUND_ENDPOINT = f"https://{BOUND_ACCOUNT}.services.ai.azure.com/api/projects/{BOUND_PROJECT}"
TRUSTED_ORCHESTRATOR = (
    "https://agent-iq-orchestrator-canary.bluefield-12345678.swedencentral.azurecontainerapps.io"
)
COMPANION_APPLICATION_ID = "11111111-1111-4111-8111-abcdefabcdef"
COMPANION_TENANT_ID = "22222222-2222-4222-8222-abcdefabcdef"


def _run_powershell(
    command: str,
    *,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    if POWERSHELL is None:
        pytest.skip("PowerShell 7 is required for deployment-script safety tests.")
    child_environment = os.environ.copy()
    if environment:
        child_environment.update(environment)
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-Command", command],
        cwd=REPOSITORY_ROOT,
        env=child_environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _assert_powershell_parseable(path: Path) -> None:
    command = r"""
$tokens = $null
$errors = $null
[System.Management.Automation.Language.Parser]::ParseFile(
    $env:TARGET_SCRIPT,
    [ref]$tokens,
    [ref]$errors
) | Out-Null
if ($errors.Count -gt 0) {
    $errors | ForEach-Object { Write-Error $_.Message }
    exit 1
}
"""
    result = _run_powershell(command, environment={"TARGET_SCRIPT": str(path)})
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.stem)
def test_foundry_scripts_parse_and_validate_before_cloud_use(script: Path) -> None:
    _assert_powershell_parseable(script)
    source = script.read_text(encoding="utf-8")

    if script in DEPLOYERS:
        canonicalize = source.index("$ProjectEndpoint = Resolve-CanonicalFoundryProjectEndpoint `")
        assert "-AccountName $AccountName `" in source[canonicalize : canonicalize + 250]
        assert "-ProjectName $ProjectName `" in source[canonicalize : canonicalize + 250]
    else:
        canonicalize = source.index(
            "$ProjectEndpoint = ConvertTo-CanonicalFoundryProjectEndpoint -Value $ProjectEndpoint"
        )
    login = source.index('. (Join-Path $PSScriptRoot "foundry_login.ps1") -Quiet')
    assert canonicalize < login
    assert canonicalize < source.index("& az ")
    if "& azd " in source:
        assert canonicalize < source.index("& azd ")


def test_foundry_login_clears_ambient_proxy_environment_before_cli_auth(
    tmp_path: Path,
) -> None:
    source = FOUNDRY_LOGIN.read_text(encoding="utf-8")
    first_clear = source.index('Remove-Item "Env:\\$name"')
    assert first_clear < source.index("& azd auth login")
    assert first_clear < source.index("& az login")
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        assert f'"{name}"' in source

    credential_file = tmp_path / "foundry.sp.env.ps1"
    credential_file.write_text(
        "\n".join(
            (
                "$env:AZURE_CLIENT_ID='synthetic-client'",
                "$env:AZURE_TENANT_ID='synthetic-tenant'",
                "$env:AZURE_CLIENT_" + "SE" + "CRET='synthetic-value'",
                "$env:AZURE_SUBSCRIPTION_ID='synthetic-subscription'",
            )
        ),
        encoding="utf-8",
    )
    command = r"""
function global:Assert-NoProxy {
    foreach ($name in @("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")) {
        if (Get-Item "Env:\$name" -ErrorAction SilentlyContinue) {
            throw "Proxy variable $name reached Azure authentication."
        }
    }
}
function global:azd {
    Assert-NoProxy
    $global:LASTEXITCODE = 0
}
function global:az {
    Assert-NoProxy
    $global:LASTEXITCODE = 0
    if (($args -join " ") -match "^account show ") {
        '{"name":"Synthetic subscription","state":"Enabled",' +
            '"tenantId":"synthetic-tenant","user":{"name":"synthetic-client"}}'
    }
}
& $env:TARGET_SCRIPT -CredentialFile $env:CREDENTIAL_FILE -Quiet
Assert-NoProxy
"""
    result = _run_powershell(
        command,
        environment={
            "TARGET_SCRIPT": str(FOUNDRY_LOGIN),
            "CREDENTIAL_FILE": str(credential_file),
            "HTTP_PROXY": "http://127.0.0.1:1",
            "HTTPS_PROXY": "http://127.0.0.1:2",
            "ALL_PROXY": "socks5://127.0.0.1:3",
        },
    )
    assert result.returncode == 0, result.stderr


def test_endpoint_rules_match_the_validated_repository_contract() -> None:
    cases = [
        {"value": VALID_ENDPOINT, "expected": VALID_ENDPOINT},
        {
            "value": "HTTPS://EXAMPLE-RESOURCE.SERVICES.AI.AZURE.COM/api/projects/Demo_1/",
            "expected": ("https://example-resource.services.ai.azure.com/api/projects/Demo_1"),
        },
        {"value": "http://example.services.ai.azure.com/api/projects/demo"},
        {"value": "https://attacker.example/api/projects/demo"},
        {"value": ("https://example.services.ai.azure.com.attacker.example/api/projects/demo")},
        {"value": "https://foo.bar.services.ai.azure.com/api/projects/demo"},
        {"value": "https://user@example.services.ai.azure.com/api/projects/demo"},
        {"value": ("https://user:" + "pass@example.services.ai.azure.com/api/projects/demo")},
        {"value": "https://example.services.ai.azure.com:443/api/projects/demo"},
        {"value": "https://example.services.ai.azure.com:8443/api/projects/demo"},
        {"value": "https://example%2eservices.ai.azure.com/api/projects/demo"},
        {"value": "https://example.services.ai.azure.com%40attacker.example/api/projects/demo"},
        {"value": "https://example.services.ai.azure.com/api/projects/%2e%2e"},
        {"value": "https://example.services.ai.azure.com/api/projects/demo%2fextra"},
        {"value": "https://example.services.ai.azure.com//api/projects/demo"},
        {"value": "https://example.services.ai.azure.com/api/projects/demo/extra"},
        {"value": "https://example.services.ai.azure.com/api/projects/demo?api-version=v1"},
        {"value": "https://example.services.ai.azure.com/api/projects/demo#fragment"},
        {"value": "https://example.services.ai.azure.com/api/projects/demo\\@attacker.example"},
        {"value": "https://example.services.ai.azure.com/api/projects/demo\nunsafe"},
    ]
    command = r"""
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:TARGET_SCRIPT,
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count -gt 0) {
    throw "Target script did not parse."
}
$functionAst = $ast.Find(
    {
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq "ConvertTo-CanonicalFoundryProjectEndpoint"
    },
    $true
)
Invoke-Expression $functionAst.Extent.Text
$cases = $env:ENDPOINT_CASES | ConvertFrom-Json
$results = @(
    foreach ($case in $cases) {
        try {
            $value = ConvertTo-CanonicalFoundryProjectEndpoint -Value $case.value
            [ordered]@{ accepted = $true; value = $value }
        }
        catch {
            [ordered]@{ accepted = $false; error = $_.Exception.Message }
        }
    }
)
[ordered]@{ results = $results } | ConvertTo-Json -Compress -Depth 5
"""
    canonicalizer_sources = []
    for script in SCRIPTS:
        result = _run_powershell(
            command,
            environment={
                "TARGET_SCRIPT": str(script),
                "ENDPOINT_CASES": json.dumps(cases),
            },
        )
        assert result.returncode == 0, result.stderr
        results = json.loads(result.stdout)["results"]
        assert results[0] == {"accepted": True, "value": VALID_ENDPOINT}
        assert results[1] == {"accepted": True, "value": cases[1]["expected"]}
        assert all(not item["accepted"] for item in results[2:])

        source = script.read_text(encoding="utf-8")
        canonicalizer_match = re.search(
            r"function ConvertTo-CanonicalFoundryProjectEndpoint \{.*?^}",
            source,
            flags=re.MULTILINE | re.DOTALL,
        )
        assert canonicalizer_match is not None
        canonicalizer_sources.append(canonicalizer_match.group(0))
    assert len(set(canonicalizer_sources)) == 1


def test_deployers_derive_endpoint_from_validated_coordinates() -> None:
    cases: list[dict[str, str | bool]] = [
        {"value": "", "accepted": True, "expected": BOUND_ENDPOINT},
        {"value": BOUND_ENDPOINT, "accepted": True, "expected": BOUND_ENDPOINT},
        {
            "value": ("HTTPS://ACCOUNT-TEST.SERVICES.AI.AZURE.COM/api/projects/project-test/"),
            "accepted": True,
            "expected": BOUND_ENDPOINT,
        },
        {"value": ("https://other-account.services.ai.azure.com/api/projects/project-test")},
        {"value": ("https://account-test.services.ai.azure.com/api/projects/other-project")},
        {"value": ("https://user@account-test.services.ai.azure.com/api/projects/project-test")},
        {"value": ("https://account-test.services.ai.azure.com:443/api/projects/project-test")},
        {"value": f"{BOUND_ENDPOINT}?api-version=v1"},
        {"value": f"{BOUND_ENDPOINT}#fragment"},
        {"value": ("https://account-test%2eservices.ai.azure.com/api/projects/project-test")},
        {"value": ("https://account-test.services.ai.azure.com/api/projects/project-test%2fother")},
    ]
    command = r"""
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:TARGET_SCRIPT,
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count -gt 0) {
    throw "Target script did not parse."
}
foreach ($name in @(
    "ConvertTo-CanonicalFoundryProjectEndpoint",
    "Get-CanonicalFoundryProjectEndpoint",
    "Resolve-CanonicalFoundryProjectEndpoint"
)) {
    $functionAst = $ast.Find(
        {
            param($node)
            $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
                $node.Name -eq $name
        },
        $true
    )
    Invoke-Expression $functionAst.Extent.Text
}
$results = @(
    foreach ($case in ($env:ENDPOINT_CASES | ConvertFrom-Json)) {
        try {
            $value = Resolve-CanonicalFoundryProjectEndpoint `
                -AccountName $env:ACCOUNT_NAME `
                -ProjectName $env:PROJECT_NAME `
                -SuppliedEndpoint $case.value
            [ordered]@{ accepted = $true; value = $value }
        }
        catch {
            [ordered]@{ accepted = $false; error = $_.Exception.Message }
        }
    }
)
[ordered]@{ results = $results } | ConvertTo-Json -Compress -Depth 5
"""
    for script in DEPLOYERS:
        result = _run_powershell(
            command,
            environment={
                "TARGET_SCRIPT": str(script),
                "ENDPOINT_CASES": json.dumps(cases),
                "ACCOUNT_NAME": BOUND_ACCOUNT,
                "PROJECT_NAME": BOUND_PROJECT,
            },
        )
        assert result.returncode == 0, result.stderr
        results = json.loads(result.stdout)["results"]
        assert results[:3] == [{"accepted": True, "value": case["expected"]} for case in cases[:3]]
        assert all(not item["accepted"] for item in results[3:])


def test_consumer_http_requests_refuse_redirects_and_check_statuses() -> None:
    for script in SCRIPTS[:2]:
        source = script.read_text(encoding="utf-8")
        request_boundary = source.index("$requestEndpoint = Assert-CanonicalFoundryProjectEndpoint")
        request_slice = source[request_boundary : request_boundary + 1800]
        assert "$response = Invoke-WebRequest" in request_slice
        assert "-MaximumRedirection 0" in source
        proxy_isolation_count = source.count("-NoProxy") + source.count("NoProxy = $true")
        assert proxy_isolation_count == source.count("Invoke-WebRequest")
        assert "-SkipHttpErrorCheck" in source
        assert "$statusCode = [int]$response.StatusCode" in request_slice
        assert "$statusCode -lt 200 -or $statusCode -gt 299" in source
        assert "-Value $Endpoint" in request_slice
        if script == CONSUMER_DEPLOYER:
            assert "-AccountName $AccountName `" in request_slice
            assert "-ProjectName $ProjectName" in request_slice
        for token_call in re.finditer(r"\$token = & az account get-access-token", source):
            preceding_boundary = source[max(0, token_call.start() - 450) : token_call.start()]
            assert "Assert-CanonicalFoundryProjectEndpoint" in preceding_boundary
            if script == CONSUMER_DEPLOYER:
                assert "-AccountName $AccountName" in preceding_boundary
                assert "-ProjectName $ProjectName" in preceding_boundary


def test_consumer_deployer_braces_variables_before_query_strings() -> None:
    source = CONSUMER_DEPLOYER.read_text(encoding="utf-8")
    assert not re.search(r"\$[A-Za-z][A-Za-z0-9]*\?api-version", source)


def test_consumer_deployer_supplies_runtime_project_coordinates() -> None:
    source = CONSUMER_DEPLOYER.read_text(encoding="utf-8")
    assert "& azd env set AGENT_IQ_AZURE_AI_ACCOUNT $AccountName" in source
    assert "& azd env set AGENT_IQ_AZURE_AI_PROJECT $ProjectName" in source


def test_consumer_artifact_channel_is_derived_before_login_or_cloud_use() -> None:
    source = CONSUMER_DEPLOYER.read_text(encoding="utf-8")

    binding = source.index("$artifactChannel = Resolve-TrustedArtifactChannel")
    login = source.index('. (Join-Path $PSScriptRoot "foundry_login.ps1") -Quiet')
    assert binding < login
    assert binding < source.index("& az ")
    assert binding < source.index("& azd ")
    assert "$env:AGENT_IQ_ARTIFACT_PUBLISH_ENDPOINT" not in source
    assert "$env:AGENT_IQ_ARTIFACT_TOKEN_SCOPE" not in source
    assert "[string]$ArtifactPublishEndpoint" not in source
    assert "[string]$ArtifactTokenScope" not in source
    assert "AGENT_IQ_ARTIFACT_TOKEN_SCOPE" not in source


def test_consumer_artifact_channel_contract_rejects_endpoint_and_identity_attacks() -> None:
    base_url_cases: list[dict[str, str | bool]] = [
        {"value": TRUSTED_ORCHESTRATOR, "accepted": True},
        {"value": ("https://attacker.bluefield-12345678.swedencentral.azurecontainerapps.io")},
        {
            "value": (
                "https://agent-iq-orchestrator-canary.greenfield-87654321."
                "swedencentral.azurecontainerapps.io"
            ),
            "accepted": True,
        },
        {"value": "https://agent-iq-orchestrator-canary.example.test"},
        {"value": f"{TRUSTED_ORCHESTRATOR}/v1/artifacts/publish"},
        {"value": TRUSTED_ORCHESTRATOR.replace("https://", "https://user@")},
        {"value": f"{TRUSTED_ORCHESTRATOR}:443"},
        {"value": f"{TRUSTED_ORCHESTRATOR}?next=https://attacker.example"},
        {"value": f"{TRUSTED_ORCHESTRATOR}#fragment"},
        {"value": TRUSTED_ORCHESTRATOR.replace(".bluefield", "%2ebluefield")},
        {"value": TRUSTED_ORCHESTRATOR.replace(".bluefield", "\\bluefield")},
    ]
    application_id_cases = [
        {"value": COMPANION_APPLICATION_ID, "accepted": True},
        {"value": "not-a-uuid"},
        {"value": "00000000-0000-0000-0000-000000000000"},
        {"value": COMPANION_APPLICATION_ID.upper()},
        {"value": f" {COMPANION_APPLICATION_ID}"},
        {"value": f"{COMPANION_APPLICATION_ID}/.default"},
    ]
    command = r"""
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:TARGET_SCRIPT,
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count -gt 0) {
    throw "Target script did not parse."
}
$trustedOrchestratorBaseUrl = $env:TRUSTED_ORCHESTRATOR
foreach ($name in @(
    "ConvertTo-TrustedOrchestratorCanaryBaseUrl",
    "Resolve-TrustedArtifactChannel"
)) {
    $functionAst = $ast.Find(
        {
            param($node)
            $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
                $node.Name -eq $name
        },
        $true
    )
    Invoke-Expression $functionAst.Extent.Text
}
$baseResults = @(
    foreach ($case in ($env:BASE_URL_CASES | ConvertFrom-Json)) {
        try {
            $channel = Resolve-TrustedArtifactChannel `
                -BaseUrl $case.value `
                -AppName "agent-iq-orchestrator-canary" `
                -ApplicationId $env:COMPANION_APPLICATION_ID `
                -TenantId $env:COMPANION_TENANT_ID
            [ordered]@{
                accepted = $true
                endpoint = $channel.PublishEndpoint
                audience = $channel.TokenAudience
                scope = $channel.TokenScope
            }
        }
        catch {
            [ordered]@{ accepted = $false; error = $_.Exception.Message }
        }
    }
)
$applicationResults = @(
    foreach ($case in ($env:APPLICATION_ID_CASES | ConvertFrom-Json)) {
        try {
            $channel = Resolve-TrustedArtifactChannel `
                -BaseUrl $env:TRUSTED_ORCHESTRATOR `
                -AppName "agent-iq-orchestrator-canary" `
                -ApplicationId $case.value `
                -TenantId $env:COMPANION_TENANT_ID
            [ordered]@{ accepted = $true; applicationId = $channel.ApplicationId }
        }
        catch {
            [ordered]@{ accepted = $false; error = $_.Exception.Message }
        }
    }
)
[ordered]@{
    baseResults = $baseResults
    applicationResults = $applicationResults
} | ConvertTo-Json -Compress -Depth 5
"""
    result = _run_powershell(
        command,
        environment={
            "TARGET_SCRIPT": str(CONSUMER_DEPLOYER),
            "TRUSTED_ORCHESTRATOR": TRUSTED_ORCHESTRATOR,
            "COMPANION_APPLICATION_ID": COMPANION_APPLICATION_ID,
            "COMPANION_TENANT_ID": COMPANION_TENANT_ID,
            "BASE_URL_CASES": json.dumps(base_url_cases),
            "APPLICATION_ID_CASES": json.dumps(application_id_cases),
        },
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    valid = payload["baseResults"][0]
    assert valid == {
        "accepted": True,
        "endpoint": f"{TRUSTED_ORCHESTRATOR}/v1/artifacts/publish",
        "audience": COMPANION_APPLICATION_ID,
        "scope": f"{COMPANION_APPLICATION_ID}/.default",
    }
    for case, item in zip(base_url_cases, payload["baseResults"], strict=True):
        assert item["accepted"] is case.get("accepted", False)
    assert payload["applicationResults"][0] == {
        "accepted": True,
        "applicationId": COMPANION_APPLICATION_ID,
    }
    assert all(not item["accepted"] for item in payload["applicationResults"][1:])


def test_host_canary_revalidates_every_endpoint_argument() -> None:
    source = HOST_CANARY.read_text(encoding="utf-8")

    for azd_call in re.finditer(r"& azd ", source):
        preceding_boundary = source[max(0, azd_call.start() - 250) : azd_call.start()]
        assert "Assert-CanonicalFoundryProjectEndpoint" in preceding_boundary
    assert "--project-endpoint $ProjectEndpoint" not in source
    assert source.count("--project-endpoint $azdProjectEndpoint") == 1
    assert source.count('"--project-endpoint", $pythonProjectEndpoint') == 3
    assert source.count('"--account-name", $AccountName') == 8
    assert source.count('"--project-name", $ProjectName') == 8
    assert source.count('"--mutation-lease-blob-url", $mutationLeaseBlobUrl') == 4
    assert "& azd ai connection create" not in source
    assert 'stateMode = "validated_read_only"' in source


def test_host_rollback_uses_explicit_validated_operation_record() -> None:
    source = HOST_CANARY.read_text(encoding="utf-8")

    validation = source.index('"validate-rollback"')
    login = source.index('. (Join-Path $PSScriptRoot "foundry_login.ps1") -Quiet')
    rollback = source.index('"rollback"', validation)
    assert validation < login < rollback
    assert '--record", $rollbackRecordPath' in source
    assert '--record", $latestPath' not in source
    assert "Rollback requires an explicit run-specific RollbackRecord." in source
    assert '[IO.Path]::GetFileName($rollbackRecordPath) -ieq "latest.json"' in source
    assert '$deploymentPath = Join-Path $runRoot "$operationId.deployment.json"' in source


@pytest.mark.parametrize(
    ("argument", "value", "error_pattern"),
    [
        ("ResourceGroup", "rg test", "resource-group"),
        ("SearchResourceGroup", "rg?search", "resource-group"),
        ("AcrResourceGroup", "rg/acr", "resource-group"),
        ("MutationLeaseResourceGroup", "rg lease", "resource-group"),
        ("SearchService", "Search_Test", "SearchService"),
        ("AcrName", "acr-test", "AcrName"),
        ("MutationLeaseStorageAccount", "Lease-Test", "StorageAccount"),
        ("MutationLeaseContainer", "Bad_Container", "MutationLeaseContainer"),
    ],
)
def test_host_invalid_cloud_coordinate_reaches_no_login_or_cloud_command(
    argument: str,
    value: str,
    error_pattern: str,
    tmp_path: Path,
) -> None:
    arguments: dict[str, object] = {
        "ProjectEndpoint": BOUND_ENDPOINT,
        "AccountName": BOUND_ACCOUNT,
        "ProjectName": BOUND_PROJECT,
        "ResourceGroup": "rg-test",
        "SearchResourceGroup": "rg-test",
        "SearchService": "search-test",
        "AcrResourceGroup": "rg-test",
        "AcrName": "acrtest",
        "MutationLeaseResourceGroup": "rg-test",
        "MutationLeaseStorageAccount": "leasestest",
        "MutationLeaseContainer": "agent-iq-locks",
        "ProbeOnly": True,
    }
    arguments[argument] = value
    command = r"""
$global:CloudCalls = 0
function global:az { $global:CloudCalls++; throw "AZ_SENTINEL" }
function global:azd { $global:CloudCalls++; throw "AZD_SENTINEL" }
function global:Invoke-RestMethod { $global:CloudCalls++; throw "REST_SENTINEL" }
function global:Invoke-WebRequest { $global:CloudCalls++; throw "WEB_SENTINEL" }
$scriptArguments = $env:SCRIPT_ARGUMENTS | ConvertFrom-Json -AsHashtable
try {
    & $env:TARGET_SCRIPT @scriptArguments
    throw "Unsafe cloud coordinate was accepted."
}
catch {
    if ($_.Exception.Message -notmatch $env:ERROR_PATTERN) {
        throw
    }
}
if ($global:CloudCalls -ne 0) {
    throw "Invalid cloud coordinate reached $global:CloudCalls cloud command(s)."
}
exit 0
"""
    result = _run_powershell(
        command,
        environment={
            "TARGET_SCRIPT": str(HOST_CANARY),
            "SCRIPT_ARGUMENTS": json.dumps(arguments),
            "ERROR_PATTERN": error_pattern,
            "USERPROFILE": str(tmp_path),
        },
    )
    assert result.returncode == 0, result.stderr


def test_host_binds_all_cloud_coordinates_before_login() -> None:
    source = HOST_CANARY.read_text(encoding="utf-8")
    login = source.index('. (Join-Path $PSScriptRoot "foundry_login.ps1") -Quiet')

    for boundary in (
        'Assert-ResourceGroupName -Name $ResourceGroup -Label "ResourceGroup"',
        'Assert-ResourceGroupName -Name $SearchResourceGroup -Label "SearchResourceGroup"',
        'Assert-ResourceGroupName -Name $AcrResourceGroup -Label "AcrResourceGroup"',
        "Assert-SearchServiceName -Name $SearchService",
        "Assert-AcrName -Name $AcrName",
        "Assert-StorageAccountName -Name $MutationLeaseStorageAccount",
        "Assert-BlobContainerName -Name $MutationLeaseContainer",
        "$mutationLeaseBlobUrl = (",
    ):
        assert source.index(boundary) < login
    assert source.index('"validate-rollback"') < login
    assert "& az storage account create" not in source
    assert "& az storage container create" not in source
    assert "& az storage blob upload" not in source


@pytest.mark.parametrize(
    ("script", "endpoint"),
    [
        (script, endpoint)
        for script in DEPLOYERS
        for endpoint in (
            ("https://other-account.services.ai.azure.com/api/projects/project-test"),
            ("https://account-test.services.ai.azure.com/api/projects/other-project"),
            ("https://user@account-test.services.ai.azure.com/api/projects/project-test"),
            ("https://account-test.services.ai.azure.com:443/api/projects/project-test"),
            f"{BOUND_ENDPOINT}?api-version=v1",
            f"{BOUND_ENDPOINT}#fragment",
            ("https://account-test%2eservices.ai.azure.com/api/projects/project-test"),
            ("https://account-test.services.ai.azure.com/api/projects/project-test%2fother"),
        )
    ],
    ids=lambda value: value.stem if isinstance(value, Path) else value,
)
def test_invalid_endpoint_reaches_no_login_token_or_cloud_command(
    script: Path,
    endpoint: str,
    tmp_path: Path,
) -> None:
    common_arguments: dict[str, object] = {
        "ProjectEndpoint": endpoint,
        "AccountName": BOUND_ACCOUNT,
        "ProjectName": BOUND_PROJECT,
    }
    if script == HOST_CANARY:
        common_arguments.update(
            {
                "ResourceGroup": "rg-test",
                "SearchService": "search-test",
                "AcrName": "acrtest",
                "MutationLeaseStorageAccount": "leasestest",
                "MutationLeaseContainer": "agent-iq-locks",
                "ProbeOnly": True,
            }
        )
    command = r"""
$global:CloudCalls = 0
function global:az {
    $global:CloudCalls++
    throw "AZ_SENTINEL"
}
function global:azd {
    $global:CloudCalls++
    throw "AZD_SENTINEL"
}
function global:Invoke-RestMethod {
    $global:CloudCalls++
    throw "REST_SENTINEL"
}
function global:Invoke-WebRequest {
    $global:CloudCalls++
    throw "WEB_SENTINEL"
}
$scriptArguments = $env:SCRIPT_ARGUMENTS | ConvertFrom-Json -AsHashtable
try {
    & $env:TARGET_SCRIPT @scriptArguments
    throw "Unsafe endpoint was accepted."
}
catch {
    if ($_.Exception.Message -notmatch "Foundry project endpoint") {
        throw
    }
}
if ($global:CloudCalls -ne 0) {
    throw "Invalid endpoint reached $global:CloudCalls cloud command(s)."
}
exit 0
"""
    result = _run_powershell(
        command,
        environment={
            "TARGET_SCRIPT": str(script),
            "SCRIPT_ARGUMENTS": json.dumps(common_arguments),
            "USERPROFILE": str(tmp_path),
        },
    )
    assert result.returncode == 0, result.stderr


def test_invalid_environment_endpoint_reaches_no_login_or_cloud_command(
    tmp_path: Path,
) -> None:
    script = HOST_CANARY
    arguments: dict[str, object] = {
        "AccountName": BOUND_ACCOUNT,
        "ProjectName": BOUND_PROJECT,
        "ResourceGroup": "rg-test",
        "SearchService": "search-test",
        "AcrName": "acrtest",
        "MutationLeaseStorageAccount": "leasestest",
        "MutationLeaseContainer": "agent-iq-locks",
        "ProbeOnly": True,
    }
    command = r"""
$global:CloudCalls = 0
function global:az { $global:CloudCalls++; throw "AZ_SENTINEL" }
function global:azd { $global:CloudCalls++; throw "AZD_SENTINEL" }
function global:Invoke-RestMethod { $global:CloudCalls++; throw "REST_SENTINEL" }
function global:Invoke-WebRequest { $global:CloudCalls++; throw "WEB_SENTINEL" }
$scriptArguments = $env:SCRIPT_ARGUMENTS | ConvertFrom-Json -AsHashtable
try {
    & $env:TARGET_SCRIPT @scriptArguments
    throw "Unsafe environment endpoint was accepted."
}
catch {
    if ($_.Exception.Message -notmatch "Foundry project endpoint") {
        throw
    }
}
if ($global:CloudCalls -ne 0) {
    throw "Invalid environment endpoint reached $global:CloudCalls cloud command(s)."
}
exit 0
"""
    result = _run_powershell(
        command,
        environment={
            "TARGET_SCRIPT": str(script),
            "SCRIPT_ARGUMENTS": json.dumps(arguments),
            "AGENT_IQ_FOUNDRY_PROJECT_ENDPOINT": (
                "https://other-account.services.ai.azure.com/api/projects/project-test"
            ),
            "USERPROFILE": str(tmp_path),
        },
    )
    assert result.returncode == 0, result.stderr


def test_deployers_revalidate_derived_endpoint_at_every_azd_boundary() -> None:
    for script in DEPLOYERS:
        source = script.read_text(encoding="utf-8")
        for azd_call in re.finditer(r"& azd ", source):
            preceding_boundary = source[max(0, azd_call.start() - 450) : azd_call.start()]
            assert "Assert-CanonicalFoundryProjectEndpoint" in preceding_boundary
            assert "-AccountName $AccountName" in preceding_boundary
            assert "-ProjectName $ProjectName" in preceding_boundary


@pytest.mark.parametrize(
    ("argument", "value"),
    [
        (
            "OrchestratorBaseUrl",
            "https://attacker.bluefield-12345678.swedencentral.azurecontainerapps.io",
        ),
        ("CompanionApplicationId", "api://attacker/.default"),
    ],
)
def test_invalid_artifact_channel_reaches_no_login_or_cloud_command(
    argument: str,
    value: str,
    tmp_path: Path,
) -> None:
    arguments = {
        "Only": ["consumer-demand-forecasting"],
        "ProjectEndpoint": VALID_ENDPOINT,
        "AccountName": "example-resource",
        "ProjectName": "example",
        "OrchestratorBaseUrl": TRUSTED_ORCHESTRATOR,
        "CompanionApplicationId": COMPANION_APPLICATION_ID,
        "CompanionTenantId": COMPANION_TENANT_ID,
    }
    arguments[argument] = value
    command = r"""
$global:CloudCalls = 0
function global:az {
    $global:CloudCalls++
    throw "AZ_SENTINEL"
}
function global:azd {
    $global:CloudCalls++
    throw "AZD_SENTINEL"
}
$scriptArguments = $env:SCRIPT_ARGUMENTS | ConvertFrom-Json -AsHashtable
try {
    & $env:TARGET_SCRIPT @scriptArguments
    throw "Unsafe artifact channel was accepted."
}
catch {
    if ($_.Exception.Message -notmatch "Artifact companion") {
        throw
    }
}
if ($global:CloudCalls -ne 0) {
    throw "Invalid artifact channel reached $global:CloudCalls cloud command(s)."
}
exit 0
"""
    result = _run_powershell(
        command,
        environment={
            "TARGET_SCRIPT": str(CONSUMER_DEPLOYER),
            "SCRIPT_ARGUMENTS": json.dumps(arguments),
            "USERPROFILE": str(tmp_path),
        },
    )
    assert result.returncode == 0, result.stderr


def test_consumer_probe_only_returns_before_mutation_enablement() -> None:
    source = CONSUMER_DEPLOYER.read_text(encoding="utf-8")

    model_prerequisite = source.index(
        "$modelState = & az cognitiveservices account deployment show"
    )
    read_only_inventory = source.index("$initialStates = @{}")
    probe_branch = source.index("if ($ProbeOnly) {\n    $probeDirectories = @{}")
    probe_return = source.index("    return", probe_branch)
    mutation_enablement = source.index("$script:ConsumerFleetPreflightComplete = $true")

    assert model_prerequisite < read_only_inventory < probe_branch < probe_return
    assert probe_return < mutation_enablement
    assert '[IO.Path]::GetFileName($resolved) -ieq "latest.json"' in source
    assert '"$runId.json"' in source
    assert '"$runId.manifest.json"' in source
    assert "& uv @probeArguments | Out-Host" in source


def test_consumer_mutation_guard_blocks_probe_only_and_failed_preflight() -> None:
    command = r"""
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:TARGET_SCRIPT,
    [ref]$tokens,
    [ref]$errors
)
$functionAst = $ast.Find(
    {
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq "Assert-ConsumerFleetMutationAllowed"
    },
    $true
)
Invoke-Expression $functionAst.Extent.Text
$global:MutationCalls = 0
function Invoke-TestMutation {
    try {
        Assert-ConsumerFleetMutationAllowed
        $global:MutationCalls++
        return $true
    }
    catch {
        return $false
    }
}
$ProbeOnly = $true
$script:ConsumerFleetPreflightComplete = $true
$probeAllowed = Invoke-TestMutation
$ProbeOnly = $false
$script:ConsumerFleetPreflightComplete = $false
$failedPreflightAllowed = Invoke-TestMutation
$script:ConsumerFleetPreflightComplete = $true
$validAllowed = Invoke-TestMutation
[ordered]@{
    probeAllowed = $probeAllowed
    failedPreflightAllowed = $failedPreflightAllowed
    validAllowed = $validAllowed
    mutationCalls = $global:MutationCalls
} | ConvertTo-Json -Compress
"""
    result = _run_powershell(
        command,
        environment={"TARGET_SCRIPT": str(CONSUMER_DEPLOYER)},
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "probeAllowed": False,
        "failedPreflightAllowed": False,
        "validAllowed": True,
        "mutationCalls": 1,
    }


def test_consumer_cloud_mutation_boundaries_are_guarded() -> None:
    source = CONSUMER_DEPLOYER.read_text(encoding="utf-8")
    guarded_functions = {
        "Ensure-Role": "& az role assignment create",
        "Ensure-Grounding": "& azd ai connection create",
        "Set-AgentTrafficVersion": "-Method Patch",
        "Remove-CreatedAgentVersion": "-Method Delete",
        "Remove-NewAgentDeployment": "-Method Delete",
        "Remove-RecordedAgentRole": "& az role assignment delete",
    }
    for function_name, mutation in guarded_functions.items():
        function_start = source.index(f"function {function_name} {{")
        mutation_start = source.index(mutation, function_start)
        guard = source.index("Assert-ConsumerFleetMutationAllowed", function_start)
        assert function_start < guard < mutation_start

    azd_up = source.index("& azd up --no-prompt")
    assert "Assert-ConsumerFleetMutationAllowed" in source[azd_up - 400 : azd_up]
    mutation_enablement = source.index("$script:ConsumerFleetPreflightComplete = $true")
    for invocation in (
        "$null = Ensure-Role `",
        "    Ensure-Grounding `",
        "        $activeState = Set-AgentTrafficVersion `",
    ):
        assert source.index(invocation, mutation_enablement) > mutation_enablement
    rollback_enablement = source.index("$script:ConsumerFleetPreflightComplete = [bool]$Rollback")
    rollback_invocation = source.index(
        "ForEach-Object { Restore-AgentDeployment -Entry $_ }",
        rollback_enablement,
    )
    assert rollback_enablement < rollback_invocation < mutation_enablement


def test_consumer_rollback_record_is_explicit_integrity_bound_and_target_bound(
    tmp_path: Path,
) -> None:
    command = r"""
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:TARGET_SCRIPT,
    [ref]$tokens,
    [ref]$errors
)
foreach ($name in @("Get-Sha256Text", "Read-ConsumerOperationRecord")) {
    $functionAst = $ast.Find(
        {
            param($node)
            $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
                $node.Name -eq $name
        },
        $true
    )
    Invoke-Expression $functionAst.Extent.Text
}
$ReportRoot = $env:REPORT_ROOT
$ProjectEndpoint = $env:PROJECT_ENDPOINT
$AccountName = $env:ACCOUNT_NAME
$ProjectName = $env:PROJECT_NAME
$directory = Join-Path $ReportRoot "consumer-operations"
New-Item -ItemType Directory -Force -Path $directory | Out-Null
$recordRunId = [guid]::NewGuid().ToString("D")
$payload = [ordered]@{
    schemaVersion = "1.0"
    evidenceType = "agent-iq.consumer-fleet-deployment"
    runId = $recordRunId
    projectEndpoint = $ProjectEndpoint
    accountName = $AccountName
    projectName = $ProjectName
    createdAt = "2026-07-14T00:00:00.0060930Z"
    agents = @([ordered]@{ agentId = "consumer-formulation-rd" })
}
$payload["integrity"] = [ordered]@{
    algorithm = "sha256"
    payloadSha256 = Get-Sha256Text -Value (
        ([ordered]@{
            schemaVersion = $payload.schemaVersion
            evidenceType = $payload.evidenceType
            runId = $payload.runId
            projectEndpoint = $payload.projectEndpoint
            accountName = $payload.accountName
            projectName = $payload.projectName
            createdAt = $payload.createdAt
            agents = $payload.agents
        }) | ConvertTo-Json -Depth 50 -Compress
    )
}
$validPath = Join-Path $directory "$recordRunId.deployment.json"
$payload | ConvertTo-Json -Depth 50 | Set-Content $validPath -Encoding utf8
$valid = Read-ConsumerOperationRecord -Path $validPath

$latestPath = Join-Path $directory "latest.json"
Copy-Item $validPath $latestPath
$latestRejected = $false
try { $null = Read-ConsumerOperationRecord -Path $latestPath }
catch { $latestRejected = $_.Exception.Message -match "explicit run record" }

$tampered = Get-Content $validPath -Raw | ConvertFrom-Json
$tampered.projectName = "other-project"
$tampered | ConvertTo-Json -Depth 50 | Set-Content $validPath -Encoding utf8
$tamperRejected = $false
try { $null = Read-ConsumerOperationRecord -Path $validPath }
catch { $tamperRejected = $_.Exception.Message -match "integrity check failed" }

$wrongTargetRunId = [guid]::NewGuid().ToString("D")
$wrongTarget = [ordered]@{
    schemaVersion = "1.0"
    evidenceType = "agent-iq.consumer-fleet-deployment"
    runId = $wrongTargetRunId
    projectEndpoint = $ProjectEndpoint
    accountName = $AccountName
    projectName = "other-project"
    createdAt = "2026-07-14T00:00:00Z"
    agents = @([ordered]@{ agentId = "consumer-formulation-rd" })
}
$wrongTarget["integrity"] = [ordered]@{
    algorithm = "sha256"
    payloadSha256 = Get-Sha256Text -Value (
        ([ordered]@{
            schemaVersion = $wrongTarget.schemaVersion
            evidenceType = $wrongTarget.evidenceType
            runId = $wrongTarget.runId
            projectEndpoint = $wrongTarget.projectEndpoint
            accountName = $wrongTarget.accountName
            projectName = $wrongTarget.projectName
            createdAt = $wrongTarget.createdAt
            agents = $wrongTarget.agents
        }) | ConvertTo-Json -Depth 50 -Compress
    )
}
$wrongTargetPath = Join-Path $directory "$wrongTargetRunId.deployment.json"
$wrongTarget | ConvertTo-Json -Depth 50 | Set-Content $wrongTargetPath -Encoding utf8
$targetRejected = $false
try { $null = Read-ConsumerOperationRecord -Path $wrongTargetPath }
catch { $targetRejected = $_.Exception.Message -match "configured Foundry target" }

[ordered]@{
    validRunId = $valid.runId
    latestRejected = $latestRejected
    tamperRejected = $tamperRejected
    targetRejected = $targetRejected
} | ConvertTo-Json -Compress
"""
    result = _run_powershell(
        command,
        environment={
            "TARGET_SCRIPT": str(CONSUMER_DEPLOYER),
            "REPORT_ROOT": str(tmp_path),
            "PROJECT_ENDPOINT": BOUND_ENDPOINT,
            "ACCOUNT_NAME": BOUND_ACCOUNT,
            "PROJECT_NAME": BOUND_PROJECT,
        },
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.splitlines()[-1])
    assert re.fullmatch(r"[0-9a-f-]{36}", payload["validRunId"])
    assert payload["latestRejected"] is True
    assert payload["tamperRejected"] is True
    assert payload["targetRejected"] is True


def test_consumer_rollback_restores_previous_traffic_and_refuses_inventory_drift() -> None:
    command = r"""
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:TARGET_SCRIPT,
    [ref]$tokens,
    [ref]$errors
)
foreach ($name in @("Assert-ExactAgentVersion", "Restore-AgentDeployment")) {
    $functionAst = $ast.Find(
        {
            param($node)
            $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
                $node.Name -eq $name
        },
        $true
    )
    Invoke-Expression $functionAst.Extent.Text
}
$knownIds = @("consumer-formulation-rd")
$entry = [pscustomobject]@{
    agentId = "consumer-formulation-rd"
    previousPresent = $true
    previousVersion = "1"
    previousVersions = @("1")
    deployedVersion = "2"
    deployedInstanceIdentity = "11111111-1111-4111-8111-111111111111"
    agentSearchRoleAssignmentId = $null
}
$script:Calls = [System.Collections.Generic.List[string]]::new()
$script:InventoryReads = 0
function Get-AgentState {
    [pscustomobject]@{ present = $true; version = "2" }
}
function Get-AgentVersionIds {
    $script:InventoryReads++
    if ($script:InventoryReads -eq 1) { return @("1", "2") }
    return @("1")
}
function Set-AgentTrafficVersion {
    param([string]$AgentId, [string]$Version)
    $script:Calls.Add("traffic:${AgentId}:$Version")
    [pscustomobject]@{ present = $true; version = $Version }
}
function Remove-CreatedAgentVersion {
    param([string]$AgentId, [string]$CreatedVersion)
    $script:Calls.Add("version:${AgentId}:$CreatedVersion")
}
function Remove-RecordedAgentRole {
    param([object]$Entry)
    $script:Calls.Add("role:$($Entry.agentId)")
}
function Remove-NewAgentDeployment { throw "unexpected new-agent deletion" }
$restored = Restore-AgentDeployment -Entry $entry
$restorationCalls = @($script:Calls)

$script:Calls.Clear()
$script:InventoryReads = 0
function Get-AgentVersionIds { return @("1", "2", "3") }
$driftRejected = $false
try { $null = Restore-AgentDeployment -Entry $entry }
catch { $driftRejected = $_.Exception.Message -match "version inventory changed" }

[ordered]@{
    action = $restored.action
    restoredVersion = $restored.restoredVersion
    calls = $restorationCalls
    driftRejected = $driftRejected
    driftMutationCount = $script:Calls.Count
} | ConvertTo-Json -Compress
"""
    result = _run_powershell(
        command,
        environment={"TARGET_SCRIPT": str(CONSUMER_DEPLOYER)},
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {
        "action": "restore_traffic",
        "restoredVersion": "1",
        "calls": [
            "traffic:consumer-formulation-rd:1",
            "version:consumer-formulation-rd:2",
            "role:consumer-formulation-rd",
        ],
        "driftRejected": True,
        "driftMutationCount": 0,
    }


def test_consumer_version_delete_accepts_empty_success_and_rejects_explicit_failure() -> None:
    command = r"""
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:TARGET_SCRIPT,
    [ref]$tokens,
    [ref]$errors
)
foreach ($name in @(
    "Get-CanonicalFoundryProjectEndpoint",
    "ConvertTo-CanonicalFoundryProjectEndpoint",
    "Assert-CanonicalFoundryProjectEndpoint",
    "Assert-ConsumerFleetMutationAllowed",
    "Assert-ExactAgentVersion",
    "Remove-CreatedAgentVersion"
)) {
    $functionAst = $ast.Find(
        {
            param($node)
            $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
                $node.Name -eq $name
        },
        $true
    )
    Invoke-Expression $functionAst.Extent.Text
}
$ProbeOnly = $false
$script:ConsumerFleetPreflightComplete = $true
$ProjectEndpoint = $env:PROJECT_ENDPOINT
$AccountName = $env:ACCOUNT_NAME
$ProjectName = $env:PROJECT_NAME
function az {
    $global:LASTEXITCODE = 0
    "synthetic-token"
}
$script:DeleteResult = $null
function Invoke-FoundryProjectRequest { return $script:DeleteResult }
Remove-CreatedAgentVersion -AgentId "consumer-formulation-rd" -CreatedVersion "2"
$emptySuccessAccepted = $true
$script:DeleteResult = [pscustomobject]@{ deleted = $false }
$explicitFailureRejected = $false
try {
    Remove-CreatedAgentVersion -AgentId "consumer-formulation-rd" -CreatedVersion "2"
}
catch {
    $explicitFailureRejected = $_.Exception.Message -match "rollback failed"
}
[ordered]@{
    emptySuccessAccepted = $emptySuccessAccepted
    explicitFailureRejected = $explicitFailureRejected
} | ConvertTo-Json -Compress
"""
    result = _run_powershell(
        command,
        environment={
            "TARGET_SCRIPT": str(CONSUMER_DEPLOYER),
            "PROJECT_ENDPOINT": BOUND_ENDPOINT,
            "ACCOUNT_NAME": BOUND_ACCOUNT,
            "PROJECT_NAME": BOUND_PROJECT,
        },
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {
        "emptySuccessAccepted": True,
        "explicitFailureRejected": True,
    }


def test_evidence_certifier_target_mismatch_precedes_login_and_cloud_calls() -> None:
    source = EVIDENCE_CERTIFIER.read_text(encoding="utf-8")
    assert source.index('throw "ProjectEndpoint does not match') < source.index("foundry_login.ps1")
    assert "& az role assignment create" not in source
    assert "& az role assignment delete" not in source

    command = r"""
$global:CloudCalls = 0
function global:az { $global:CloudCalls++; throw "AZ_SENTINEL" }
function global:uv { $global:CloudCalls++; throw "UV_SENTINEL" }
try {
    & $env:TARGET_SCRIPT `
        -ProjectEndpoint $env:PROJECT_ENDPOINT `
        -AccountName $env:ACCOUNT_NAME `
        -ProjectName $env:PROJECT_NAME
    throw "Mismatched target was accepted."
}
catch {
    if ($_.Exception.Message -notmatch "does not match AccountName") {
        throw
    }
}
if ($global:CloudCalls -ne 0) {
    throw "Mismatched evidence target reached $global:CloudCalls cloud call(s)."
}
exit 0
"""
    result = _run_powershell(
        command,
        environment={
            "TARGET_SCRIPT": str(EVIDENCE_CERTIFIER),
            "PROJECT_ENDPOINT": (
                "https://other-account.services.ai.azure.com/api/projects/project-test"
            ),
            "ACCOUNT_NAME": BOUND_ACCOUNT,
            "PROJECT_NAME": BOUND_PROJECT,
        },
    )
    assert result.returncode == 0, result.stderr


def test_orchestration_certifier_target_mismatch_precedes_login_and_cloud_calls() -> None:
    source = ORCHESTRATION_CERTIFIER.read_text(encoding="utf-8")
    assert source.index('throw "ProjectEndpoint does not match') < source.index("foundry_login.ps1")
    assert "& az role assignment create" not in source
    assert "& az role assignment delete" not in source
    assert "--account-name $AccountName" in source
    assert "--project-name $ProjectName" in source

    command = r"""
$global:CloudCalls = 0
function global:az { $global:CloudCalls++; throw "AZ_SENTINEL" }
function global:uv { $global:CloudCalls++; throw "UV_SENTINEL" }
try {
    & $env:TARGET_SCRIPT `
        -ProjectEndpoint $env:PROJECT_ENDPOINT `
        -AccountName $env:ACCOUNT_NAME `
        -ProjectName $env:PROJECT_NAME
    throw "Mismatched target was accepted."
}
catch {
    if ($_.Exception.Message -notmatch "does not match AccountName") {
        throw
    }
}
if ($global:CloudCalls -ne 0) {
    throw "Mismatched orchestration target reached $global:CloudCalls cloud call(s)."
}
exit 0
"""
    result = _run_powershell(
        command,
        environment={
            "TARGET_SCRIPT": str(ORCHESTRATION_CERTIFIER),
            "PROJECT_ENDPOINT": (
                "https://other-account.services.ai.azure.com/api/projects/project-test"
            ),
            "ACCOUNT_NAME": BOUND_ACCOUNT,
            "PROJECT_NAME": BOUND_PROJECT,
        },
    )
    assert result.returncode == 0, result.stderr


def _host_deployment_record(report_root: Path) -> str:
    from agent_iq.deployments import foundry_host

    digest = f"sha256:{'a' * 64}"
    image = f"acrtest.azurecr.io/agent-iq/foundry-langgraph-host@{digest}"
    plan = foundry_host.create_host_plan(
        logical_agent_id="consumer-formulation-rd",
        deployment_agent_id="consumer-formulation-rd-host-canary",
        project_endpoint=BOUND_ENDPOINT,
        account_name=BOUND_ACCOUNT,
        project_name=BOUND_PROJECT,
        image=image,
        model_deployment="gpt-5.4-mini",
        search_service="search-test",
    ).model_dump(mode="json", by_alias=True)
    deployment = {
        "schemaVersion": "1.0",
        "status": "active",
        "buildMode": "container",
        "logicalAgentId": "consumer-formulation-rd",
        "deploymentAgentId": "consumer-formulation-rd-host-canary",
        "version": "7",
        "instancePrincipalId": "agent-principal",
        "projectEndpoint": BOUND_ENDPOINT,
        "image": image,
        "imageDigest": digest,
        "rollback": {"action": "delete_agent"},
        "plan": plan,
    }
    report_root.mkdir(parents=True)
    (report_root / "latest.json").write_text(
        json.dumps(
            {
                "schemaVersion": "1.0",
                "status": "pass",
                "plan": plan,
                "deployment": deployment,
            }
        ),
        encoding="utf-8",
    )
    return json.dumps(plan)


def _run_host_with_stubbed_cloud(
    tmp_path: Path,
    *,
    arguments: dict[str, object],
    image_resolves: bool,
    expected_error: str = "",
) -> dict[str, object]:
    report_root = tmp_path / "reports"
    plan_json = _host_deployment_record(report_root)
    plan = json.loads(plan_json)
    credential_dir = tmp_path / "foundry-azd"
    credential_dir.mkdir()
    (credential_dir / "foundry.sp.env.ps1").write_text(
        "\n".join(
            (
                "$env:AZURE_CLIENT_ID='synthetic-client'",
                "$env:AZURE_TENANT_ID='synthetic-tenant'",
                "$env:AZURE_CLIENT_" + "SE" + "CRET='synthetic-value'",
                "$env:AZURE_SUBSCRIPTION_ID='synthetic-subscription'",
            )
        ),
        encoding="utf-8",
    )
    script_arguments: dict[str, object] = {
        "ResourceGroup": "rg-test",
        "AccountName": BOUND_ACCOUNT,
        "ProjectName": BOUND_PROJECT,
        "ProjectEndpoint": BOUND_ENDPOINT,
        "SearchResourceGroup": "rg-test",
        "SearchService": "search-test",
        "AcrName": "acrtest",
        "MutationLeaseStorageAccount": "leasestest",
        "MutationLeaseContainer": "agent-iq-locks",
        "ReportRoot": str(report_root),
    }
    script_arguments.update(arguments)
    command = r"""
$global:Mutations = [System.Collections.Generic.List[string]]::new()
function global:Get-TestArgument {
    param([object[]]$Values, [string]$Name)
    $index = [Array]::IndexOf($Values, $Name)
    if ($index -lt 0 -or $index + 1 -ge $Values.Count) {
        throw "Missing stub argument $Name"
    }
    return [string]$Values[$index + 1]
}
function global:az {
    $values = @($args)
    $joined = $values -join " "
    $global:LASTEXITCODE = 0
    if ($joined -match "^role assignment create ") {
        $global:Mutations.Add("az role assignment create")
        return
    }
    if ($joined -match "^account show ") {
        '{"name":"Synthetic subscription","state":"Enabled",' +
            '"tenantId":"synthetic-tenant","user":{"name":"synthetic-client"}}'
        return
    }
    if ($joined -match "^role assignment delete ") {
        $global:Mutations.Add("az role assignment delete")
        return
    }
    if ($joined -match "^role assignment list ") {
        "role-id"
        return
    }
    if ($joined -match "^cognitiveservices account deployment show ") {
        "Succeeded"
        return
    }
    if ($joined -match "^group show ") {
        $name = Get-TestArgument -Values $values -Name "--name"
        "/subscriptions/synthetic-subscription/resourceGroups/$name"
        return
    }
    if ($joined -match "^cognitiveservices account show ") {
        '{"id":"/subscriptions/synthetic-subscription/resourceGroups/rg-test/providers/' +
            'Microsoft.CognitiveServices/accounts/account-test"}'
        return
    }
    if ($joined -match "^rest ") {
        '{"id":"/subscriptions/synthetic-subscription/resourceGroups/rg-test/providers/' +
            'Microsoft.CognitiveServices/accounts/account-test/projects/project-test",' +
            '"identity":{"principalId":"project-principal"}}'
        return
    }
    if ($joined -match "^acr show ") {
        '{"id":"/subscriptions/synthetic-subscription/resourceGroups/rg-test/providers/' +
            'Microsoft.ContainerRegistry/registries/acrtest",' +
            '"loginServer":"acrtest.azurecr.io",' +
            '"roleAssignmentMode":"LegacyRegistryPermissions"}'
        return
    }
    if ($joined -match "^search service show ") {
        '{"id":"/subscriptions/synthetic-subscription/resourceGroups/rg-test/providers/' +
            'Microsoft.Search/searchServices/search-test"}'
        return
    }
    if ($joined -match "^storage account show ") {
        '{"id":"/subscriptions/synthetic-subscription/resourceGroups/rg-test/providers/' +
            'Microsoft.Storage/storageAccounts/leasestest"}'
        return
    }
    if ($joined -match "^storage blob exists ") {
        '{"exists":true}'
        return
    }
    if ($joined -match "^acr manifest show-metadata ") {
        if ($env:IMAGE_RESOLVES -eq "true") {
            $env:IMAGE_DIGEST
        }
        else {
            $global:LASTEXITCODE = 1
        }
        return
    }
    if ($joined -match "^login " -or $joined -match "^account set ") {
        return
    }
    throw "Unexpected az command: $joined"
}
function global:azd {
    $values = @($args)
    $joined = $values -join " "
    $global:LASTEXITCODE = 0
    if ($joined -match "^ai connection create ") {
        $global:Mutations.Add("azd ai connection create")
        return
    }
    if ($joined -match "^ai connection show ") {
        '{"target":"https://search-test.search.windows.net/knowledgebases/' +
            'consumer-product-innovation-kb/mcp?api-version=2026-05-01-preview",' +
            '"authType":"ProjectManagedIdentity"}'
        return
    }
    if ($joined -match "^auth login ") {
        return
    }
    throw "Unexpected azd command: $joined"
}
function global:docker {
    $values = @($args)
    $joined = $values -join " "
    $global:LASTEXITCODE = 0
    if ($joined -match "^(push|build|buildx) ") {
        $global:Mutations.Add("docker $($values[0])")
    }
}
function global:uv {
    $values = @($args)
    $global:LASTEXITCODE = 0
    $moduleIndex = [Array]::IndexOf($values, "-m")
    $module = [string]$values[$moduleIndex + 1]
    if ($module -eq "agent_iq.deployments.foundry_host") {
        $operation = [string]$values[$moduleIndex + 2]
        if ($operation -in @("deploy", "rollback")) {
            $global:Mutations.Add("foundry-host $operation")
            return
        }
        $output = Get-TestArgument -Values $values -Name "--output"
        if ($operation -eq "plan") {
            $env:PLAN_JSON | Set-Content $output -Encoding utf8
            $cardOutput = Get-TestArgument -Values $values -Name "--card-output"
            $env:CARD_JSON | Set-Content $cardOutput -Encoding utf8
            return
        }
        if ($operation -eq "probe-toolbox") {
            '{"status":"pass","knowledgeRetrievePresent":true}' |
                Set-Content $output -Encoding utf8
            return
        }
        if ($operation -eq "probe-responses") {
            $version = Get-TestArgument -Values $values -Name "--agent-version"
            [ordered]@{ status = "pass"; agentVersion = $version } |
                ConvertTo-Json | Set-Content $output -Encoding utf8
            return
        }
    }
    if ($module -eq "agent_iq.deployments.foundry_canary") {
        $output = Get-TestArgument -Values $values -Name "--output"
        '{"status":"pass","agent":{"version":"7"}}' |
            Set-Content $output -Encoding utf8
        return
    }
    throw "Unexpected uv command: $($values -join ' ')"
}
$scriptArguments = $env:SCRIPT_ARGUMENTS | ConvertFrom-Json -AsHashtable
$succeeded = $false
$errorMessage = ""
try {
    & $env:TARGET_SCRIPT @scriptArguments
    $succeeded = $true
}
catch {
    $errorMessage = $_.Exception.Message
}
if ($env:EXPECTED_ERROR -and $errorMessage -notmatch $env:EXPECTED_ERROR) {
    throw "Expected '$($env:EXPECTED_ERROR)', received '$errorMessage'."
}
if (-not $env:EXPECTED_ERROR -and -not $succeeded) {
    throw $errorMessage
}
[ordered]@{
    succeeded = $succeeded
    error = $errorMessage
    mutations = @($global:Mutations)
} | ConvertTo-Json -Compress
"""
    result = _run_powershell(
        command,
        environment={
            "TARGET_SCRIPT": str(HOST_CANARY),
            "SCRIPT_ARGUMENTS": json.dumps(script_arguments),
            "IMAGE_RESOLVES": str(image_resolves).lower(),
            "IMAGE_DIGEST": plan["imageDigest"],
            "PLAN_JSON": plan_json,
            "CARD_JSON": json.dumps(plan["agentCard"]),
            "EXPECTED_ERROR": expected_error,
            "USERPROFILE": str(tmp_path),
        },
    )
    assert result.returncode == 0, result.stderr
    parsed: object = json.loads(result.stdout.splitlines()[-1])
    assert isinstance(parsed, dict)
    return {str(key): value for key, value in parsed.items()}


def test_host_probe_only_stubs_all_mutations_and_performs_none(tmp_path: Path) -> None:
    result = _run_host_with_stubbed_cloud(
        tmp_path,
        arguments={"ProbeOnly": True},
        image_resolves=True,
    )

    assert result == {"succeeded": True, "error": "", "mutations": []}


@pytest.mark.parametrize(
    ("existing_image", "image_resolves", "error_pattern"),
    [
        (
            "acrtest.azurecr.io/agent-iq/foundry-langgraph-host:mutable",
            True,
            "exact digest",
        ),
        (
            (f"acrtest.azurecr.io/agent-iq/foundry-langgraph-host@sha256:{'b' * 64}"),
            False,
            "could not be resolved",
        ),
    ],
)
def test_host_invalid_or_unresolved_image_reaches_no_mutation(
    tmp_path: Path,
    existing_image: str,
    image_resolves: bool,
    error_pattern: str,
) -> None:
    result = _run_host_with_stubbed_cloud(
        tmp_path,
        arguments={"SkipBuild": True, "ExistingImage": existing_image},
        image_resolves=image_resolves,
        expected_error=error_pattern,
    )

    assert result["succeeded"] is False
    assert result["mutations"] == []


def test_host_validation_and_probe_return_precede_mutation_phase() -> None:
    source = HOST_CANARY.read_text(encoding="utf-8")
    probe_branch = source.index("if ($ProbeOnly) {")
    probe_return = source.index("    return", probe_branch)
    mutation_phase = source.index(
        "# Mutation phase: every deployment prerequisite above has completed successfully."
    )
    immutable_resolution = source.index(
        "$immutableImage = Resolve-VerifiedImmutableImage",
        probe_return,
    )
    descriptor_validation = source.index(
        "Test-DescriptorImage -Image $immutableImage",
        immutable_resolution,
    )
    built_digest = source.index('$builtDigest = [string]$buildMetadata."containerimage.digest"')
    image_push = source.index("& docker push $taggedImage")

    assert probe_branch < probe_return < immutable_resolution < descriptor_validation
    assert descriptor_validation < mutation_phase
    assert built_digest < image_push < mutation_phase
    assert source.index("    Require-Role `") < mutation_phase
    for mutation in ('    "deploy",',):
        assert source.index(mutation) > mutation_phase
    assert "& azd ai connection create" not in source
    assert "function Ensure-Role {" not in source
    assert "& az role assignment create" not in source
    assert "& az role assignment delete" not in source
    assert "@latest" not in source


@pytest.mark.parametrize(
    ("arguments", "error_pattern"),
    [
        ({"Runs": 0}, "Runs must be at least one"),
        ({"ProbeOnly": True, "SkipProbe": True}, "ProbeOnly cannot be combined"),
        ({"ProbeOnly": True, "Rollback": True}, "mutually exclusive"),
        ({"Rollback": True}, "explicit run-specific"),
    ],
)
def test_consumer_local_prerequisite_failure_reaches_no_cloud_command(
    arguments: dict[str, object],
    error_pattern: str,
    tmp_path: Path,
) -> None:
    command = r"""
$global:CloudCalls = 0
function global:az { $global:CloudCalls++; throw "AZ_SENTINEL" }
function global:azd { $global:CloudCalls++; throw "AZD_SENTINEL" }
function global:Invoke-RestMethod { $global:CloudCalls++; throw "REST_SENTINEL" }
function global:Invoke-WebRequest { $global:CloudCalls++; throw "WEB_SENTINEL" }
$scriptArguments = $env:SCRIPT_ARGUMENTS | ConvertFrom-Json -AsHashtable
try {
    & $env:TARGET_SCRIPT @scriptArguments
    throw "Invalid prerequisites were accepted."
}
catch {
    if ($_.Exception.Message -notmatch $env:ERROR_PATTERN) {
        throw
    }
}
if ($global:CloudCalls -ne 0) {
    throw "Prerequisite failure reached $global:CloudCalls cloud command(s)."
}
exit 0
"""
    result = _run_powershell(
        command,
        environment={
            "TARGET_SCRIPT": str(CONSUMER_DEPLOYER),
            "SCRIPT_ARGUMENTS": json.dumps(arguments),
            "ERROR_PATTERN": error_pattern,
            "USERPROFILE": str(tmp_path),
        },
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("arguments", "error_pattern"),
    [
        ({}, "Supply exactly one"),
        (
            {
                "ReportPath": ["consumer-formulation-rd/latest.json"],
                "ReportRoot": str(REPOSITORY_ROOT / "reports"),
            },
            "latest.json is forbidden",
        ),
    ],
)
def test_consumer_summary_rejects_implicit_evidence_before_login(
    arguments: dict[str, object],
    error_pattern: str,
    tmp_path: Path,
) -> None:
    command = r"""
$global:CloudCalls = 0
function global:az { $global:CloudCalls++; throw "AZ_SENTINEL" }
function global:azd { $global:CloudCalls++; throw "AZD_SENTINEL" }
function global:Invoke-RestMethod { $global:CloudCalls++; throw "REST_SENTINEL" }
$scriptArguments = $env:SCRIPT_ARGUMENTS | ConvertFrom-Json -AsHashtable
try {
    & $env:TARGET_SCRIPT @scriptArguments
    throw "Implicit evidence was accepted."
}
catch {
    if ($_.Exception.Message -notmatch $env:ERROR_PATTERN) {
        throw
    }
}
if ($global:CloudCalls -ne 0) {
    throw "Rejected evidence reached $global:CloudCalls cloud command(s)."
}
exit 0
"""
    result = _run_powershell(
        command,
        environment={
            "TARGET_SCRIPT": str(SCRIPTS[1]),
            "SCRIPT_ARGUMENTS": json.dumps(arguments),
            "ERROR_PATTERN": error_pattern,
            "USERPROFILE": str(tmp_path),
        },
    )
    assert result.returncode == 0, result.stderr


def test_consumer_evidence_helpers_reject_replay_collision_and_tampering() -> None:
    command = r"""
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:TARGET_SCRIPT,
    [ref]$tokens,
    [ref]$errors
)
foreach ($name in @(
    "Get-Sha256Text",
    "Add-PayloadIntegrity",
    "Assert-PayloadIntegrity",
    "Assert-FreshTimestamp",
    "Assert-CanonicalRunIdentity",
    "Assert-ExpectedEvidenceIdentity",
    "Assert-ExactConsumerCoverage",
    "Add-UniqueEvidenceBinding"
)) {
    $functionAst = $ast.Find(
        {
            param($node)
            $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
                $node.Name -eq $name
        },
        $true
    )
    Invoke-Expression $functionAst.Extent.Text
}
$MaxEvidenceAgeMinutes = 60
$runId = "11111111-1111-4111-8111-111111111111"
$otherRunId = "22222222-2222-4222-8222-222222222222"
$nonce = "a" * 64
$endpoint = "https://example-resource.services.ai.azure.com/api/projects/example"
$evidence = [pscustomobject]@{
    runId = $runId
    nonce = $nonce
    agentId = "consumer-a"
    projectEndpoint = $endpoint
}
$results = [ordered]@{}

$payload = [ordered]@{ status = "pass"; value = 1 }
$payload = Add-PayloadIntegrity -Payload $payload
$roundTrip = ($payload | ConvertTo-Json -Depth 10) | ConvertFrom-Json
$roundTrip.value = 2
try {
    Assert-PayloadIntegrity -Payload $roundTrip -Description "tampered"
    $results.tamperedRejected = $false
}
catch {
    $results.tamperedRejected = $true
}

try {
    Assert-FreshTimestamp `
        -Timestamp ([DateTimeOffset]::UtcNow.AddHours(-2)) `
        -Now ([DateTimeOffset]::UtcNow) `
        -Description "stale"
    $results.staleRejected = $false
}
catch {
    $results.staleRejected = $true
}

try {
    Assert-ExpectedEvidenceIdentity `
        -Evidence $evidence `
        -ExpectedProjectEndpoint $endpoint `
        -ExpectedRunId $otherRunId `
        -ExpectedNonce $nonce `
        -ExpectedAgentId "consumer-a"
    $results.crossRunRejected = $false
}
catch {
    $results.crossRunRejected = $true
}

try {
    Assert-ExpectedEvidenceIdentity `
        -Evidence $evidence `
        -ExpectedProjectEndpoint $endpoint `
        -ExpectedRunId $runId `
        -ExpectedNonce $nonce `
        -ExpectedAgentId "consumer-b"
    $results.crossAgentRejected = $false
}
catch {
    $results.crossAgentRejected = $true
}

try {
    Assert-ExactConsumerCoverage `
        -ExpectedAgentIds @("consumer-a", "consumer-b") `
        -ActualAgentIds @("consumer-a") `
        -Description "missing"
    $results.missingRejected = $false
}
catch {
    $results.missingRejected = $true
}

$paths = [Collections.Generic.HashSet[string]]::new(
    [StringComparer]::OrdinalIgnoreCase
)
$agents = [Collections.Generic.HashSet[string]]::new(
    [StringComparer]::Ordinal
)
Add-UniqueEvidenceBinding `
    -Path "C:\reports\run-a.json" `
    -AgentId "consumer-a" `
    -SeenPaths $paths `
    -SeenAgentIds $agents
try {
    Add-UniqueEvidenceBinding `
        -Path "c:\REPORTS\run-a.json" `
        -AgentId "consumer-b" `
        -SeenPaths $paths `
        -SeenAgentIds $agents
    $results.pathCollisionRejected = $false
}
catch {
    $results.pathCollisionRejected = $true
}
try {
    Add-UniqueEvidenceBinding `
        -Path "C:\reports\run-b.json" `
        -AgentId "consumer-a" `
        -SeenPaths $paths `
        -SeenAgentIds $agents
    $results.duplicateAgentRejected = $false
}
catch {
    $results.duplicateAgentRejected = $true
}
$results | ConvertTo-Json -Compress
"""
    result = _run_powershell(
        command,
        environment={"TARGET_SCRIPT": str(SCRIPTS[1])},
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "tamperedRejected": True,
        "staleRejected": True,
        "crossRunRejected": True,
        "crossAgentRejected": True,
        "missingRejected": True,
        "pathCollisionRejected": True,
        "duplicateAgentRejected": True,
    }


def test_consumer_summary_cross_checks_reported_immutable_state_with_live_state() -> None:
    source = SCRIPTS[1].read_text(encoding="utf-8")

    assert "latest.json is forbidden" in source
    assert ".versions.latest" not in source
    assert "Get-FixedRoutedVersion" in source
    assert "traffic is not fixed 100% to one immutable version" in source
    assert "/versions/$([uri]::EscapeDataString($validatedVersion))" in source
    assert '"trafficBoundToCertifiedVersion"' in source
    assert "Get-FileHash -LiteralPath $resolvedPath" in source
    assert "$evidence.deployment.$property -cne $live.$property" in source
    for field in (
        '"version"',
        '"codeContentHash"',
        '"image"',
        '"imageDigest"',
        '"instanceIdentity"',
    ):
        assert field in source
    assert '"$runId.json"' in source
    assert 'Join-Path $destination "latest.json"' not in source


def test_consumer_summary_fixed_traffic_validation_is_fail_closed() -> None:
    command = r"""
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:TARGET_SCRIPT,
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count -gt 0) { throw "Target script did not parse." }
foreach ($name in @(
    "Assert-FoundryPathSegment",
    "Get-ObjectPropertyValue",
    "Get-FixedRoutedVersion"
)) {
    $functionAst = $ast.Find(
        {
            param($node)
            $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
                $node.Name -eq $name
        },
        $true
    )
    Invoke-Expression $functionAst.Extent.Text
}
function New-Rule([string]$Type, [int]$Percentage, [string]$Version) {
    return [pscustomobject]@{
        type = $Type
        agentVersion = $Version
        trafficPercentage = $Percentage
    }
}
function New-Details([object[]]$Rules) {
    return [pscustomobject]@{
        agentEndpoint = [pscustomobject]@{
            versionSelector = [pscustomobject]@{
                versionSelectionRules = $Rules
            }
        }
    }
}
$valid = Get-FixedRoutedVersion `
    -Payload (New-Details @((New-Rule "FixedRatio" 100 "2"))) `
    -AgentId "consumer-formulation-rd"
$rejected = @()
foreach ($rules in @(
    @(),
    @((New-Rule "FixedRatio" 50 "2")),
    @((New-Rule "Weighted" 100 "2")),
    @((New-Rule "FixedRatio" 100 "2"), (New-Rule "FixedRatio" 0 "3")),
    @([pscustomobject]@{
        type = "FixedRatio"
        agentVersion = "2"
        trafficPercentage = "100"
    }),
    @([pscustomobject]@{
        type = "FixedRatio"
        agentVersion = "2"
        trafficPercentage = [double]100
    })
)) {
    try {
        $null = Get-FixedRoutedVersion `
            -Payload (New-Details $rules) `
            -AgentId "consumer-formulation-rd"
        $rejected += $false
    }
    catch {
        $rejected += $true
    }
}
[ordered]@{ valid = $valid; rejected = $rejected } |
    ConvertTo-Json -Compress -Depth 5
"""
    result = _run_powershell(
        command,
        environment={"TARGET_SCRIPT": str(SCRIPTS[1])},
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "valid": "2",
        "rejected": [True, True, True, True, True, True],
    }
