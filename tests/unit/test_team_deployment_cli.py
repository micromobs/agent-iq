"""Offline contract tests for the public Agent IQ deployment experience."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
POWERSHELL = shutil.which("pwsh")
ROOT_CLI = REPOSITORY_ROOT / "scripts" / "agent-iq.ps1"
PROVISIONER = REPOSITORY_ROOT / "scripts" / "provision_team_azure.ps1"
SUBSCRIPTION_ID = "-".join(("11111111", "1111", "4111", "8111", "111111111111"))
OTHER_SUBSCRIPTION_ID = "-".join(("22222222", "2222", "4222", "8222", "222222222222"))


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    if POWERSHELL is None:
        pytest.skip("PowerShell 7 is required for deployment CLI tests.")
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-File", *arguments],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("script", [ROOT_CLI, PROVISIONER], ids=lambda path: path.stem)
def test_public_deployment_scripts_parse(script: Path) -> None:
    if POWERSHELL is None:
        pytest.skip("PowerShell 7 is required for deployment CLI tests.")
    command = (
        "$tokens=$null;$errors=$null;"
        "[void][System.Management.Automation.Language.Parser]::ParseFile("
        f"'{script}',[ref]$tokens,[ref]$errors);"
        "if($errors.Count){$errors|ForEach-Object{Write-Error $_.Message};exit 1}"
    )
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-Command", command],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_greenfield_plan_is_deterministic_and_contains_no_credentials(tmp_path: Path) -> None:
    arguments = (
        str(PROVISIONER),
        "-EnvironmentName",
        "dev",
        "-SubscriptionId",
        SUBSCRIPTION_ID,
        "-Location",
        "swedencentral",
        "-OutputDirectory",
        str(tmp_path),
        "-Plan",
    )
    first = _run(*arguments)
    second = _run(*arguments)
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout

    plan = json.loads(first.stdout)
    assert plan["subscriptionId"] == SUBSCRIPTION_ID
    assert plan["resourceGroup"].startswith("rg-aiq-dev-")
    assert re.fullmatch(r"aiqdev[a-f0-9]{8}", plan["acrName"])
    assert plan["foundryProjectEndpoint"].endswith("/api/projects/agent-iq-dev")
    assert plan["orchestratorAppName"] == "agent-iq-orchestrator-canary"
    assert plan["artifactCompanionApplicationId"] == "<created-during-provisioning>"
    assert re.fullmatch(
        r"Agent IQ dev [a-f0-9]{8} Artifact Companion",
        plan["artifactCompanionDisplayName"],
    )
    assert "secret" not in first.stdout.casefold()
    assert not any(tmp_path.iterdir())


def test_root_cli_deploy_plan_performs_no_mutation(tmp_path: Path) -> None:
    environment_file = tmp_path / "environment.ps1"
    result = _run(
        str(ROOT_CLI),
        "deploy",
        "-Environment",
        "dev",
        "-SubscriptionId",
        SUBSCRIPTION_ID,
        "-Location",
        "swedencentral",
        "-EnvironmentFile",
        str(environment_file),
        "-Plan",
    )
    assert result.returncode == 0, result.stderr
    assert "No Azure resource was changed." in result.stdout
    assert str(tmp_path) in result.stdout.replace("\\\\", "\\")
    assert not any(tmp_path.iterdir())


def test_root_cli_package_builds_tenant_bound_host_artifacts(tmp_path: Path) -> None:
    environment_file = tmp_path / "environment.ps1"
    endpoint = "https://agent-iq-orchestrator.example.azurecontainerapps.io"
    environment_file.write_text(
        "\n".join(
            (
                f"$env:AZURE_SUBSCRIPTION_ID = '{SUBSCRIPTION_ID}'",
                f"$env:AGENT_IQ_ORCHESTRATOR_BASE_URL = '{endpoint}'",
                f"$env:AGENT_IQ_COWORK_APP_ID = '{SUBSCRIPTION_ID}'",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    result = _run(
        str(ROOT_CLI),
        "package",
        "-EnvironmentFile",
        str(environment_file),
        "-PluginVersion",
        "2.0.0",
        "-AllowAnonymousDemoConnector",
    )

    assert result.returncode == 0, result.stderr
    cowork_package = tmp_path / "agent-iq.zip"
    copilot_plugin = tmp_path / "copilot" / "agent-iq-plugin" / ".mcp.json"
    assert cowork_package.is_file()
    assert copilot_plugin.is_file()
    with zipfile.ZipFile(cowork_package) as archive:
        cowork_manifest = json.loads(archive.read("manifest.json"))
    cowork_remote = cowork_manifest["agentConnectors"][0]["toolSource"]["remoteMcpServer"]
    assert cowork_manifest["version"] == "2.0.0"
    assert cowork_remote["mcpServerUrl"] == f"{endpoint}/mcp"
    copilot_mcp = json.loads(copilot_plugin.read_text(encoding="utf-8"))
    assert copilot_mcp["mcpServers"]["agent-iq"]["url"] == f"{endpoint}/mcp"
    assert (tmp_path / "reports" / "cowork" / "latest.json").is_file()
    assert (tmp_path / "reports" / "copilot" / "latest.json").is_file()


def test_root_cli_exposes_complete_lifecycle_and_safety_guards() -> None:
    source = ROOT_CLI.read_text(encoding="utf-8")
    for command in (
        "doctor",
        "install",
        "demo",
        "deploy",
        "package",
        "test",
        "status",
        "destroy",
    ):
        assert f'"{command}"' in source
    assert "COST WARNING" in source
    assert "Type DEPLOY to continue" in source
    assert "Type DESTROY to continue" in source
    assert "artifactCompanionManaged" in source
    assert "artifactCompanionOwnershipMarker" in source
    assert "[string]$app.ownershipMarker -ceq $ownershipMarker" in source
    assert '[string]$SubscriptionId = ""' in source
    assert '[version]"2.80.0"' in source
    assert "az group delete" in source
    assert '-ExpectedAgent "employee-it-helpdesk"' in source
    assert '-ExpectedSourceId "employee-vpn-recovery-guidance"' in source
    assert "[switch]$AllowAnonymousDemoConnector" in source
    assert "It is not an organization-production authentication mode." in source


def test_root_cli_requires_anonymous_demo_opt_in_before_cloud_access() -> None:
    result = _run(str(ROOT_CLI), "deploy", "-Yes")

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "-AllowAnonymousDemoConnector" in output
    assert "Azure CLI" not in output


@pytest.mark.parametrize(
    "relative",
    (
        Path("deployments/container-apps/orchestrator-canary/Dockerfile"),
        Path("deployments/container-apps/artifact-canary/Dockerfile"),
    ),
)
def test_canary_images_are_pinned_locked_and_non_root(relative: Path) -> None:
    dockerfile = (REPOSITORY_ROOT / relative).read_text(encoding="utf-8")

    assert "@sha256:" in dockerfile.splitlines()[0]
    assert "COPY pyproject.toml uv.lock README.md ./" in dockerfile
    assert "uv sync --frozen --no-dev" in dockerfile
    assert "USER agentiq" in dockerfile


def test_foundation_uses_keyless_identities_and_no_acr_admin() -> None:
    source = PROVISIONER.read_text(encoding="utf-8")
    assert '"--admin-enabled", "false"' in source
    assert '"--assign-identity"' in source
    assert '"--identity-type", "SystemAssigned"' in source
    assert "Search Index Data Reader" in source
    assert "Cognitive Services User" in source
    assert "AcrPush" in source
    assert '"--service-management-reference", $artifactCompanionOwnershipMarker' in source
    assert "is not owned by this Agent IQ environment" in source
    assert "$displayName = $artifactCompanionDisplayName" in source
    assert "Contains no credentials" in source
    assert "--password" not in source
    assert '--admin-enabled", "true' not in source


def test_destroy_rejects_subscription_mismatch_before_loading_environment(
    tmp_path: Path,
) -> None:
    environment_file = tmp_path / "environment.ps1"
    environment_file.write_text(
        "throw 'Environment must not be loaded after a subscription mismatch.'\n",
        encoding="utf-8",
    )
    (tmp_path / "deployment.json").write_text(
        json.dumps(
            {
                "subscriptionId": SUBSCRIPTION_ID,
                "resourceGroup": "rg-aiq-dev-safe",
            }
        ),
        encoding="utf-8",
    )

    result = _run(
        str(ROOT_CLI),
        "destroy",
        "-EnvironmentFile",
        str(environment_file),
        "-SubscriptionId",
        OTHER_SUBSCRIPTION_ID,
        "-Yes",
    )

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "does not match deployment record subscription" in output
    assert "Environment must not be loaded" not in output
