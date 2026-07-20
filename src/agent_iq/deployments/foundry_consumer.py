"""Generate and validate current Foundry hosted-agent projects for the consumer fleet."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

from agent_iq.catalog import (
    ConsumerRuntimeAssets,
    foundry_card_projection,
    load_consumer_runtime_assets,
)
from agent_iq.catalog.assets import RuntimeAgentDefinition
from agent_iq.contracts import AgentDescriptor, ToolBackend

SOURCE_SAMPLE_COMMIT = "2cff8906062e9dccca305372805123c11a8058ee"  # pragma: allowlist secret
SOURCE_SAMPLE_URL = (
    "https://github.com/microsoft-foundry/foundry-samples/tree/"
    f"{SOURCE_SAMPLE_COMMIT}/samples/python/hosted-agents/langgraph/a2a/a2a-executor"
)
AZURE_YAML_SCHEMA = (
    "https://raw.githubusercontent.com/Azure/azure-dev/main/schemas/v1.0/azure.yaml.json"
)
FOUNDRY_IQ_API_VERSION = "2026-05-01-preview"
REQUIRED_EXTENSION = ">=1.0.0-beta.4"
RESPONSES_VERSION = "2.0.0"
GENERATED_MARKER = ".agent-iq-build.json"
DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_MODEL_VERSION = "2026-03-17"
DEFAULT_MODEL_SKU = "GlobalStandard"
DEFAULT_MODEL_CAPACITY = 10
_GRAPH_SMOKE_TIMEOUT_SECONDS = 300

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_OUTPUT_ROOT = _REPOSITORY_ROOT / "build" / "foundry" / "consumer"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_GUID = re.compile(r"(?i)\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b")
_LITERAL_CREDENTIAL = re.compile(
    r"""(?ix)
    \b(?:api[_-]?key|client[_-]?secret|access[_-]?token)\b
    \s*[:=]\s*
    ["'][^<{$\s][^"']{7,}["']
    """
)
_UNSUPPORTED_A2A = re.compile(r"(?i)(?:\ba2a\b[^\r\n]{0,80}\b0\.2\b|\b0\.2\b[^\r\n]{0,80}\ba2a\b)")
_LEGACY_PUBLIC_NAME = "pine" + "apple"
_RETIRED_PACKAGES = (
    "azure-ai-agentserver-langgraph",
    "azure-ai-agentserver-agentframework",
)
_GENERATED_REQUIREMENTS = """\
aiohttp==3.14.1
azure-identity==1.25.3
httpx==0.28.1
langchain==1.3.12
langchain-azure-ai[hosting]==1.2.8
langchain-mcp-adapters==0.3.0
langgraph==1.2.9
mcp==1.28.1
pydantic==2.13.4
pydantic-settings==2.14.2
"""
_PYDANTIC_AI_REQUIREMENT = "pydantic-ai-slim[openai]==2.9.0"


class GenerationError(RuntimeError):
    """A deployment artifact cannot be generated safely."""


class ValidationError(RuntimeError):
    """A generated deployment artifact violates the current contract."""


@dataclass(frozen=True, slots=True)
class GeneratedArtifact:
    agent_id: str
    path: Path
    build_hash: str
    file_count: int
    written: bool


@dataclass(frozen=True, slots=True)
class ValidatedArtifact:
    agent_id: str
    path: Path
    build_hash: str
    file_count: int


@dataclass(frozen=True, slots=True)
class GenerationOptions:
    model: str = DEFAULT_MODEL
    model_version: str = DEFAULT_MODEL_VERSION
    model_sku: str = DEFAULT_MODEL_SKU
    model_capacity: int = DEFAULT_MODEL_CAPACITY

    def validate(self) -> None:
        for label, value in (
            ("model", self.model),
            ("model version", self.model_version),
            ("model SKU", self.model_sku),
        ):
            if not _SAFE_NAME.fullmatch(value):
                raise GenerationError(f"Invalid {label} {value!r}.")
        if self.model_capacity < 1:
            raise GenerationError("Model capacity must be at least one.")


def generate_consumer_deployments(
    output_root: Path = _DEFAULT_OUTPUT_ROOT,
    *,
    agent_ids: Sequence[str] | None = None,
    options: GenerationOptions | None = None,
    dry_run: bool = False,
) -> tuple[GeneratedArtifact, ...]:
    """Generate deterministic, independently deployable projects."""

    active_options = options or GenerationOptions()
    active_options.validate()
    root = output_root.resolve()
    _reject_source_output(root)
    assets = load_consumer_runtime_assets()
    descriptors = _select_descriptors(assets, agent_ids)
    generated: list[GeneratedArtifact] = []

    for descriptor in descriptors:
        files = _artifact_files(descriptor, assets, active_options)
        build_hash = _build_hash(files)
        target = root / descriptor.agent_id
        generated.append(
            GeneratedArtifact(
                agent_id=descriptor.agent_id,
                path=target,
                build_hash=build_hash,
                file_count=len(files),
                written=not dry_run,
            )
        )
        if dry_run:
            continue
        _write_artifact(
            target,
            files,
            _build_record(descriptor, assets, active_options, files, build_hash),
        )
    return tuple(generated)


def validate_consumer_deployments(
    output_root: Path,
    *,
    agent_ids: Sequence[str] | None = None,
    graph_smoke: bool = True,
) -> tuple[ValidatedArtifact, ...]:
    """Validate generated files, current preview contracts, and import closure."""

    root = output_root.resolve()
    assets = load_consumer_runtime_assets()
    descriptors = _select_descriptors(assets, agent_ids)
    results: list[ValidatedArtifact] = []
    for descriptor in descriptors:
        target = root / descriptor.agent_id
        results.append(
            _validate_artifact(
                target,
                descriptor,
                assets,
            )
        )
    if graph_smoke:
        _smoke_generated_graphs(root, descriptors)
    return tuple(results)


def _artifact_files(
    descriptor: AgentDescriptor,
    assets: ConsumerRuntimeAssets,
    options: GenerationOptions,
) -> dict[str, bytes]:
    slug = descriptor.agent_id
    source_root = f"src/{slug}"
    files = {
        "azure.yaml": _azure_yaml(
            descriptor,
            _runtime_for(descriptor.agent_id, assets),
            options,
        ).encode("utf-8"),
        "toolbox.yaml": _toolbox_yaml(descriptor, assets).encode("utf-8"),
        "agent-card.json": _json_bytes(_card_payload(descriptor)),
        ".env.example": _env_example(descriptor, assets, options).encode("utf-8"),
        "README.md": _readme(descriptor, assets, options).encode("utf-8"),
        "scripts/provision_toolbox.ps1": _provision_toolbox_script(
            descriptor,
        ).encode("utf-8"),
        f"{source_root}/main.py": _main_py(slug).encode("utf-8"),
        f"{source_root}/requirements.txt": _requirements(descriptor).encode("utf-8"),
    }
    for relative in _vendor_source_paths(descriptor):
        destination = f"{source_root}/agent_iq/{relative.as_posix()}"
        files[destination] = (_PACKAGE_ROOT / relative).read_bytes()
    return files


def _vendor_source_paths(descriptor: AgentDescriptor) -> tuple[Path, ...]:
    paths = [
        Path("__init__.py"),
        Path("py.typed"),
        Path("config.py"),
        Path("errors.py"),
        Path("publication.py"),
        Path("tls.py"),
        Path("connectors/__init__.py"),
        Path("connectors/entra.py"),
        Path("connectors/foundry_toolbox.py"),
        Path("connectors/sap.py"),
        Path("runtime/__init__.py"),
        Path("runtime/hero_tools.py"),
        Path("runtime/hosted_consumer.py"),
        Path("runtime/telemetry.py"),
        Path("data/__init__.py"),
        Path("data/consumer/agents.json"),
        Path("data/consumer/runtime.json"),
        Path("data/consumer/knowledge_bases.json"),
        Path("data/consumer/sap_fixtures.json"),
    ]
    if descriptor.implementation.framework == "PydanticAI":
        paths.append(Path("runtime/pydantic_consumer.py"))
    source_contract = Path("data/consumer/corpora") / f"{descriptor.agent_id}.jsonl"
    if (_PACKAGE_ROOT / source_contract).is_file():
        paths.append(source_contract)
    paths.extend(
        path.relative_to(_PACKAGE_ROOT)
        for path in sorted((_PACKAGE_ROOT / "catalog").glob("*.py"))
        if path.name != "cards.py"
    )
    paths.extend(
        path.relative_to(_PACKAGE_ROOT)
        for path in sorted((_PACKAGE_ROOT / "contracts").glob("*.py"))
    )
    return tuple(paths)


def _card_payload(descriptor: AgentDescriptor) -> dict[str, Any]:
    return foundry_card_projection(descriptor)


def _azure_yaml(
    descriptor: AgentDescriptor,
    runtime: RuntimeAgentDefinition,
    options: GenerationOptions,
) -> str:
    slug = descriptor.agent_id
    service = {
        "host": "azure.ai.agent",
        "metadata": {"tags": _framework_tags(descriptor)},
        "project": f"src/{slug}",
        "language": "python",
        "codeConfiguration": {
            "runtime": "python_3_13",
            "entryPoint": "main.py",
        },
        "uses": ["ai-project"],
        "kind": "hosted",
        "name": slug,
        "displayName": f"{descriptor.name} - Agent IQ",
        "description": descriptor.description,
        "protocols": [
            {
                "protocol": "responses",
                "version": RESPONSES_VERSION,
            }
        ],
        "environmentVariables": [
            {
                "name": "AGENT_IQ_AZURE_AI_ACCOUNT",
                "value": "${AGENT_IQ_AZURE_AI_ACCOUNT}",
            },
            {
                "name": "AGENT_IQ_AZURE_AI_PROJECT",
                "value": "${AGENT_IQ_AZURE_AI_PROJECT}",
            },
            {
                "name": "AZURE_AI_MODEL_DEPLOYMENT_NAME",
                "value": "${AZURE_AI_MODEL_DEPLOYMENT_NAME}",
            },
            {
                "name": "TOOLBOX_NAME",
                "value": _toolbox_name(slug),
            },
        ]
        + (
            [
                {
                    "name": "AGENT_IQ_SAP_MODE",
                    "value": "fixture",
                }
            ]
            if _uses_sap(runtime)
            else []
        )
        + (
            [
                {
                    "name": "AGENT_IQ_ARTIFACT_PUBLISH_ENDPOINT",
                    "value": "${AGENT_IQ_ARTIFACT_PUBLISH_ENDPOINT}",
                },
                {
                    "name": "AGENT_IQ_ARTIFACT_COMPANION_APPLICATION_ID",
                    "value": "${AGENT_IQ_ARTIFACT_COMPANION_APPLICATION_ID}",
                },
                {
                    "name": "AGENT_IQ_ARTIFACT_COMPANION_TENANT_ID",
                    "value": "${AGENT_IQ_ARTIFACT_COMPANION_TENANT_ID}",
                },
            ]
            if runtime.artifact_output_kinds
            else []
        ),
        "container": {
            "resources": {
                "cpu": "0.5",
                "memory": "1Gi",
            }
        },
        "agentEndpoint": {
            "protocols": [
                "responses",
                "a2a",
            ]
        },
        "agentCard": _card_payload(descriptor),
    }
    payload = {
        "requiredVersions": {
            "extensions": {
                "azure.ai.agents": REQUIRED_EXTENSION,
            }
        },
        "name": slug,
        "services": {
            "ai-project": {
                "host": "azure.ai.project",
                "deployments": [
                    {
                        "name": options.model,
                        "model": {
                            "format": "OpenAI",
                            "name": options.model,
                            "version": options.model_version,
                        },
                        "sku": {
                            "name": options.model_sku,
                            "capacity": options.model_capacity,
                        },
                    }
                ],
            },
            slug: service,
        },
        "infra": {
            "provider": "microsoft.foundry",
        },
    }
    return (
        f"# yaml-language-server: $schema={AZURE_YAML_SCHEMA}\n"
        f"# Pattern source: {SOURCE_SAMPLE_URL}\n\n" + _yaml_text(payload)
    )


def _toolbox_yaml(
    descriptor: AgentDescriptor,
    assets: ConsumerRuntimeAssets,
) -> str:
    runtime = _runtime_for(descriptor.agent_id, assets)
    payload = {
        "description": (
            f"Foundry IQ knowledge base for {descriptor.name}; exposes "
            "knowledge_base_retrieve through managed MCP."
        ),
        "tools": [
            {
                "type": "mcp",
                "name": "knowledge_base",
                "server_label": "knowledge_base",
                "server_url": "${KB_MCP_ENDPOINT}",
                "project_connection_id": _connection_name(descriptor.agent_id),
                "require_approval": "never",
            }
        ],
    }
    return (
        f"# Knowledge binding: {runtime.knowledge_binding_id}\n"
        "# Authentication: Foundry project/agent managed identity.\n" + _yaml_text(payload)
    )


def _main_py(agent_id: str) -> str:
    return f'''\
"""Generated Agent IQ hosted entrypoint for {agent_id}."""

from __future__ import annotations

import os

from agent_iq.runtime.hosted_consumer import build_hosted_server

EXPECTED_AGENT_ID = "{agent_id}"

def main() -> None:
    configured_agent_id = os.environ.get("A2A_AGENT_ID", EXPECTED_AGENT_ID).strip()
    if configured_agent_id != EXPECTED_AGENT_ID:
        raise RuntimeError(
            f"Artifact for {{EXPECTED_AGENT_ID!r}} cannot host {{configured_agent_id!r}}."
        )
    port = int(os.environ.get("PORT", "8088"))
    build_hosted_server(EXPECTED_AGENT_ID).run(port=port)


if __name__ == "__main__":
    main()
'''


def _env_example(
    descriptor: AgentDescriptor,
    assets: ConsumerRuntimeAssets,
    options: GenerationOptions,
) -> str:
    binding = _runtime_for(descriptor.agent_id, assets).knowledge_binding_id
    sap_mode = (
        "\n# DETERMINISTIC FIXTURE - NOT LIVE SAP.\nAGENT_IQ_SAP_MODE=fixture"
        if _uses_sap(_runtime_for(descriptor.agent_id, assets))
        else ""
    )
    artifact_channel = (
        "\n# Authenticated Agent IQ companion channel.\n"
        "AGENT_IQ_ARTIFACT_PUBLISH_ENDPOINT=https://"
        "agent-iq-orchestrator-canary.<aca-env>.<region>.azurecontainerapps.io"
        "/v1/artifacts/publish\n"
        "AGENT_IQ_ARTIFACT_COMPANION_APPLICATION_ID=<entra-application-client-id>\n"
        "AGENT_IQ_ARTIFACT_COMPANION_TENANT_ID=<entra-tenant-id>"
        if _runtime_for(descriptor.agent_id, assets).artifact_output_kinds
        else ""
    )
    return f"""\
# Values are supplied to azd or the generated toolbox provisioning script.
AGENT_IQ_AZURE_AI_ACCOUNT=<azure-ai-account>
AGENT_IQ_AZURE_AI_PROJECT=<foundry-project>
AZURE_AI_MODEL_DEPLOYMENT_NAME={options.model}
TOOLBOX_NAME={_toolbox_name(descriptor.agent_id)}
KB_MCP_ENDPOINT=https://<search-service>.search.windows.net/knowledgebases/{binding}/mcp?api-version={FOUNDRY_IQ_API_VERSION}
{sap_mode}
{artifact_channel}
"""


def _readme(
    descriptor: AgentDescriptor,
    assets: ConsumerRuntimeAssets,
    options: GenerationOptions,
) -> str:
    runtime = _runtime_for(descriptor.agent_id, assets)
    framework = descriptor.implementation.framework or "unspecified"
    builder = (
        "PydanticAI Agent with strict input/output models and dependency-injected tools"
        if framework == "PydanticAI"
        else "langchain.agents.create_agent"
    )
    return f"""\
# {descriptor.name}

Generated Agent IQ project for a {framework} agent hosted on Microsoft Foundry.
It serves Responses {RESPONSES_VERSION}, exposes native incoming A2A, and
grounds answers through the `{runtime.knowledge_binding_id}` Foundry IQ
knowledge base via a managed toolbox MCP connection.

## Current preview baseline

- `azure.ai.agents` extension: `{REQUIRED_EXTENSION}`
- Authoring framework: `{framework}`
- Foundry host adapter: `langchain_azure_ai.agents.hosting.ResponsesHostServer`
- Agent builder: `{builder}`
- Agent Card target: A2A v1.0
- Foundry IQ API: `{FOUNDRY_IQ_API_VERSION}`
- Model: `{options.model}` version `{options.model_version}` on `{options.model_sku}`
- Source sample commit: `{SOURCE_SAMPLE_COMMIT}`

## Configure

Set `KB_MCP_ENDPOINT` to the knowledge base MCP URL shown in `.env.example`.
The Foundry project endpoint is injected by the platform and must not be set in
`azure.yaml`.

## Provision the keyless toolbox

```powershell
pwsh .\\scripts\\provision_toolbox.ps1 -KnowledgeBaseMcpEndpoint $env:KB_MCP_ENDPOINT
```

The script creates a RemoteTool connection using agentic identity and a toolbox
containing only `knowledge_base_retrieve`. It does not use API keys.

## Deploy

```powershell
azd env set AGENT_IQ_AZURE_AI_ACCOUNT <azure-ai-account>
azd env set AGENT_IQ_AZURE_AI_PROJECT <foundry-project>
azd env set AZURE_AI_MODEL_DEPLOYMENT_NAME {options.model}
azd up
```

All deployment coordinates live in the selected azd environment, not this
artifact. Generate and validate artifacts from the Agent IQ repository rather
than editing generated files.
"""


def _provision_toolbox_script(descriptor: AgentDescriptor) -> str:
    connection = _connection_name(descriptor.agent_id)
    toolbox = _toolbox_name(descriptor.agent_id)
    return f"""\
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^https://")]
    [string]$KnowledgeBaseMcpEndpoint
)

$ErrorActionPreference = "Stop"
$toolboxFile = Join-Path (Split-Path -Parent $PSScriptRoot) "toolbox.yaml"
$env:KB_MCP_ENDPOINT = $KnowledgeBaseMcpEndpoint

& azd ai connection create {connection} --kind remote-tool --force `
    --target $KnowledgeBaseMcpEndpoint --auth-type agentic-identity `
    --audience https://search.azure.com --metadata "ApiType=Azure"
if ($LASTEXITCODE -ne 0) {{
    throw "Foundry IQ managed connection creation failed."
}}

$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& azd ai toolbox show {toolbox} *> $null
$toolboxExists = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $previousErrorAction
if (-not $toolboxExists) {{
    $resolved = (Get-Content $toolboxFile -Raw).Replace(
        '${{KB_MCP_ENDPOINT}}',
        $KnowledgeBaseMcpEndpoint
    )
    $temporary = Join-Path $env:TEMP "{toolbox}-$PID.yaml"
    Set-Content -Path $temporary -Value $resolved -NoNewline
    try {{
        & azd ai toolbox create {toolbox} --from-file $temporary
        if ($LASTEXITCODE -ne 0) {{
            throw "Foundry toolbox creation failed."
        }}
    }}
    finally {{
        Remove-Item $temporary -Force -ErrorAction SilentlyContinue
    }}
}}
"""


def _build_record(
    descriptor: AgentDescriptor,
    assets: ConsumerRuntimeAssets,
    options: GenerationOptions,
    files: dict[str, bytes],
    build_hash: str,
) -> dict[str, Any]:
    runtime = _runtime_for(descriptor.agent_id, assets)
    return {
        "schemaVersion": "1.0",
        "agentId": descriptor.agent_id,
        "buildHash": build_hash,
        "fileCount": len(files),
        "files": {
            name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())
        },
        "model": {
            "deployment": options.model,
            "version": options.model_version,
            "sku": options.model_sku,
            "capacity": options.model_capacity,
        },
        "knowledgeBindingId": runtime.knowledge_binding_id,
        "responsesVersion": RESPONSES_VERSION,
        "a2aCardVersion": descriptor.version.version,
        "sourceSampleCommit": SOURCE_SAMPLE_COMMIT,
    }


def _write_artifact(
    target: Path,
    files: dict[str, bytes],
    build_record: dict[str, Any],
) -> None:
    if target.exists() and not (target / GENERATED_MARKER).is_file():
        raise GenerationError(
            f"Refusing to replace unmarked directory {target}. Choose another --out path."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp-{os.getpid()}")
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True)
    try:
        for name, content in files.items():
            path = temporary / Path(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        (temporary / GENERATED_MARKER).write_bytes(_json_bytes(build_record))
        if target.exists():
            shutil.rmtree(target)
        _replace_directory(temporary, target)
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def _replace_directory(source: Path, target: Path) -> None:
    for attempt in range(8):
        try:
            source.replace(target)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(0.05 * (attempt + 1))


def _validate_artifact(
    target: Path,
    descriptor: AgentDescriptor,
    assets: ConsumerRuntimeAssets,
) -> ValidatedArtifact:
    if not target.is_dir():
        raise ValidationError(f"Missing generated artifact {target}.")
    deprecated = [
        path.name
        for path in target.rglob("*")
        if path.is_file() and path.name in {"agent.yaml", "agent.manifest.yaml"}
    ]
    if deprecated:
        raise ValidationError(f"{descriptor.agent_id}: deprecated manifest {deprecated[0]!r}.")
    caches = [path for path in target.rglob("__pycache__")]
    if caches:
        raise ValidationError(f"{descriptor.agent_id}: generated source cache is forbidden.")

    marker = _load_json_mapping(target / GENERATED_MARKER)
    if marker.get("agentId") != descriptor.agent_id:
        raise ValidationError(f"{descriptor.agent_id}: build marker agent mismatch.")
    files = _read_artifact_files(target)
    _validate_hashes(descriptor.agent_id, files, marker)
    _validate_text_content(descriptor.agent_id, files)
    _validate_azure_yaml(target / "azure.yaml", descriptor, assets)
    _validate_toolbox(target / "toolbox.yaml", descriptor, assets)
    _validate_card(target / "agent-card.json", descriptor)
    _validate_requirements(target, descriptor)
    _validate_vendored_source(target, descriptor)
    _compile_python(target, descriptor.agent_id)

    return ValidatedArtifact(
        agent_id=descriptor.agent_id,
        path=target,
        build_hash=str(marker["buildHash"]),
        file_count=len(files),
    )


def _read_artifact_files(target: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for path in sorted(target.rglob("*")):
        if path.is_file() and path.name != GENERATED_MARKER:
            files[path.relative_to(target).as_posix()] = path.read_bytes()
    return files


def _validate_hashes(
    agent_id: str,
    files: dict[str, bytes],
    marker: dict[str, Any],
) -> None:
    actual_hash = _build_hash(files)
    if marker.get("buildHash") != actual_hash:
        raise ValidationError(f"{agent_id}: deterministic build hash mismatch.")
    if marker.get("fileCount") != len(files):
        raise ValidationError(f"{agent_id}: generated file count mismatch.")
    expected_files = marker.get("files")
    if not isinstance(expected_files, dict):
        raise ValidationError(f"{agent_id}: build marker lacks file hashes.")
    actual_files = {
        name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())
    }
    if expected_files != actual_files:
        raise ValidationError(f"{agent_id}: generated file digest mismatch.")


def _validate_text_content(agent_id: str, files: dict[str, bytes]) -> None:
    for name, content in files.items():
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError(f"{agent_id}: non-text generated file {name}.") from exc
        lowered = text.casefold()
        if _LEGACY_PUBLIC_NAME in lowered:
            raise ValidationError(f"{agent_id}: legacy public name found in {name}.")
        if _UNSUPPORTED_A2A.search(text):
            raise ValidationError(f"{agent_id}: unsupported A2A 0.2 found in {name}.")
        if _GUID.search(text):
            raise ValidationError(f"{agent_id}: environment coordinate found in {name}.")
        if _LITERAL_CREDENTIAL.search(text):
            raise ValidationError(f"{agent_id}: literal credential found in {name}.")
        for package in _RETIRED_PACKAGES:
            if package in lowered:
                raise ValidationError(f"{agent_id}: retired preview package found in {name}.")
        if "knowledgebaseretrievalclient" in lowered:
            raise ValidationError(f"{agent_id}: direct Search retrieval found in {name}.")
        if "foundry_iq_token" in lowered:
            raise ValidationError(f"{agent_id}: static Foundry IQ token found in {name}.")


def _validate_azure_yaml(
    path: Path,
    descriptor: AgentDescriptor,
    assets: ConsumerRuntimeAssets | None = None,
) -> None:
    effective_assets = assets or load_consumer_runtime_assets()
    payload = _load_yaml_mapping(path)
    required_versions = _mapping(payload.get("requiredVersions"), "requiredVersions")
    extensions = _mapping(required_versions.get("extensions"), "extensions")
    if extensions.get("azure.ai.agents") != REQUIRED_EXTENSION:
        raise ValidationError(f"{descriptor.agent_id}: stale azure.ai.agents extension floor.")

    services = _mapping(payload.get("services"), "services")
    project = _mapping(services.get("ai-project"), "ai-project")
    if project.get("host") != "azure.ai.project":
        raise ValidationError(f"{descriptor.agent_id}: missing Foundry project service.")
    service = _mapping(services.get(descriptor.agent_id), descriptor.agent_id)
    if service.get("host") != "azure.ai.agent" or service.get("kind") != "hosted":
        raise ValidationError(f"{descriptor.agent_id}: invalid hosted-agent service.")
    if service.get("project") != f"src/{descriptor.agent_id}":
        raise ValidationError(f"{descriptor.agent_id}: source project path mismatch.")
    if service.get("protocols") != [{"protocol": "responses", "version": RESPONSES_VERSION}]:
        raise ValidationError(f"{descriptor.agent_id}: Responses 2.0.0 is required.")

    environment = _list(service.get("environmentVariables"), "environmentVariables")
    env_names = {str(_mapping(item, "environment variable").get("name")) for item in environment}
    expected_env = {
        "AGENT_IQ_AZURE_AI_ACCOUNT",
        "AGENT_IQ_AZURE_AI_PROJECT",
        "AZURE_AI_MODEL_DEPLOYMENT_NAME",
        "TOOLBOX_NAME",
    }
    if _uses_sap(_runtime_for(descriptor.agent_id, effective_assets)):
        expected_env.add("AGENT_IQ_SAP_MODE")
    if _runtime_for(descriptor.agent_id, effective_assets).artifact_output_kinds:
        expected_env.update(
            {
                "AGENT_IQ_ARTIFACT_PUBLISH_ENDPOINT",
                "AGENT_IQ_ARTIFACT_COMPANION_APPLICATION_ID",
                "AGENT_IQ_ARTIFACT_COMPANION_TENANT_ID",
            }
        )
    if env_names != expected_env:
        raise ValidationError(f"{descriptor.agent_id}: hosted environment variables drifted.")
    if "FOUNDRY_PROJECT_ENDPOINT" in env_names:
        raise ValidationError(f"{descriptor.agent_id}: platform endpoint must be injected.")

    endpoint = _mapping(service.get("agentEndpoint"), "agentEndpoint")
    if endpoint.get("protocols") != ["responses", "a2a"]:
        raise ValidationError(f"{descriptor.agent_id}: native incoming A2A is required.")
    if service.get("agentCard") != _card_payload(descriptor):
        raise ValidationError(f"{descriptor.agent_id}: azure.yaml Agent Card drifted.")


def _validate_toolbox(
    path: Path,
    descriptor: AgentDescriptor,
    assets: ConsumerRuntimeAssets,
) -> None:
    payload = _load_yaml_mapping(path)
    tools = _list(payload.get("tools"), "tools")
    if len(tools) != 1:
        raise ValidationError(f"{descriptor.agent_id}: toolbox must expose exactly one tool.")
    tool = _mapping(tools[0], "tool")
    expected = {
        "type": "mcp",
        "name": "knowledge_base",
        "server_label": "knowledge_base",
        "server_url": "${KB_MCP_ENDPOINT}",
        "project_connection_id": _connection_name(descriptor.agent_id),
        "require_approval": "never",
    }
    if tool != expected:
        raise ValidationError(f"{descriptor.agent_id}: Foundry IQ toolbox drifted.")
    binding = _runtime_for(descriptor.agent_id, assets).knowledge_binding_id
    env_text = (path.parent / ".env.example").read_text(encoding="utf-8")
    expected_path = f"/knowledgebases/{binding}/mcp?api-version={FOUNDRY_IQ_API_VERSION}"
    if expected_path not in env_text:
        raise ValidationError(f"{descriptor.agent_id}: knowledge-base endpoint drifted.")


def _validate_card(path: Path, descriptor: AgentDescriptor) -> None:
    if _load_json_mapping(path) != _card_payload(descriptor):
        raise ValidationError(f"{descriptor.agent_id}: generated Agent Card drifted.")


def _validate_requirements(target: Path, descriptor: AgentDescriptor) -> None:
    agent_id = descriptor.agent_id
    path = target / "src" / agent_id / "requirements.txt"
    lines = {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    required = {
        "langchain-azure-ai[hosting]==1.2.8",
        "langchain-mcp-adapters==0.3.0",
        "mcp==1.28.1",
    }
    if not required.issubset(lines):
        raise ValidationError(f"{agent_id}: hosted preview dependency pins drifted.")
    has_pydantic_ai = _PYDANTIC_AI_REQUIREMENT in lines
    expects_pydantic_ai = descriptor.implementation.framework == "PydanticAI"
    if has_pydantic_ai != expects_pydantic_ai:
        raise ValidationError(f"{agent_id}: framework dependency pins drifted.")
    if any(line.casefold().startswith("azure-search-documents") for line in lines):
        raise ValidationError(f"{agent_id}: direct Search SDK is forbidden in hosted runtime.")


def _compile_python(target: Path, agent_id: str) -> None:
    for path in target.rglob("*.py"):
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except SyntaxError as exc:
            raise ValidationError(f"{agent_id}: Python syntax error in {path.name}.") from exc


def _validate_vendored_source(target: Path, descriptor: AgentDescriptor) -> None:
    agent_id = descriptor.agent_id
    source = target / "src" / agent_id
    main_path = source / "main.py"
    if main_path.read_text(encoding="utf-8") != _main_py(agent_id):
        raise ValidationError(f"{agent_id}: generated entrypoint drifted.")
    for relative in _vendor_source_paths(descriptor):
        generated = source / "agent_iq" / relative
        canonical = _PACKAGE_ROOT / relative
        if not generated.is_file() or generated.read_bytes() != canonical.read_bytes():
            raise ValidationError(f"{agent_id}: vendored source drifted at {relative.as_posix()}.")


def _smoke_generated_graphs(root: Path, descriptors: Sequence[AgentDescriptor]) -> None:
    smoke_descriptor = next(
        (
            descriptor
            for descriptor in descriptors
            if descriptor.implementation.framework == "PydanticAI"
        ),
        descriptors[0],
    )
    source = root / smoke_descriptor.agent_id / "src" / smoke_descriptor.agent_id
    graph_specs = tuple(
        (descriptor.agent_id, descriptor.implementation.framework) for descriptor in descriptors
    )
    script = f"""
from collections.abc import Sequence
from typing import Any
from pathlib import Path
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool, StructuredTool
from agent_iq.catalog import load_consumer_runtime_assets
from agent_iq.connectors.sap import FixtureSapReader
from agent_iq.runtime.hero_tools import build_agent_tools
from agent_iq.runtime.hosted_consumer import create_consumer_graph, definition_for
import agent_iq
import main

GRAPH_SPECS = {graph_specs!r}

class ToolModel(GenericFakeChatModel):
    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Any | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ):
        return self

def knowledge_base_retrieve(query: str) -> str:
    return "Grounded test result [ref_id:0]"

tool = StructuredTool.from_function(
    knowledge_base_retrieve,
    name="knowledge_base_retrieve",
    description="Retrieve grounded knowledge.",
)
assets = load_consumer_runtime_assets()
for agent_id, framework in GRAPH_SPECS:
    try:
        definition = definition_for(agent_id, assets)
        tools = build_agent_tools(
            definition.runtime,
            [tool],
            sap_reader=FixtureSapReader(assets.sap_fixtures),
        )
        if framework == "PydanticAI":
            from pydantic_ai.models.test import TestModel
            from agent_iq.runtime.pydantic_consumer import create_pydantic_graph
            graph = create_pydantic_graph(
                agent_id,
                model=TestModel(call_tools=[]),
                tools=tools,
                assets=assets,
            )
        else:
            model = ToolModel(messages=iter([AIMessage(content="ok")]))
            graph = create_consumer_graph(agent_id, model=model, tools=tools, assets=assets)
        assert graph is not None
    except Exception as exc:
        raise RuntimeError(f"{{agent_id}}: generated graph construction failed") from exc
assert main.EXPECTED_AGENT_ID == {smoke_descriptor.agent_id!r}
assert Path(agent_iq.__file__).resolve().is_relative_to(Path.cwd().resolve())
"""
    environment = os.environ.copy()
    for key in tuple(environment):
        if key.startswith("COV_CORE_") or key == "COVERAGE_PROCESS_START":
            environment.pop(key)
    environment["PYTHONPATH"] = str(source)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=source,
            env=environment,
            capture_output=True,
            text=True,
            timeout=_GRAPH_SMOKE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValidationError(
            "Generated graph smoke timed out after "
            f"{_GRAPH_SMOKE_TIMEOUT_SECONDS} seconds for {len(descriptors)} agents."
        ) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        message = detail[-1] if detail else f"exit code {result.returncode}"
        raise ValidationError(f"Generated graph smoke failed: {message}")


def _select_descriptors(
    assets: ConsumerRuntimeAssets,
    agent_ids: Sequence[str] | None,
) -> tuple[AgentDescriptor, ...]:
    by_id = {agent.agent_id: agent for agent in assets.catalog.agents}
    selected_ids = sorted(by_id) if agent_ids is None else _normalize_ids(agent_ids)
    unknown = [agent_id for agent_id in selected_ids if agent_id not in by_id]
    if unknown:
        raise GenerationError(f"Unknown consumer agent ID {unknown[0]!r}.")
    if not selected_ids:
        raise GenerationError("At least one agent must be selected.")
    return tuple(by_id[agent_id] for agent_id in selected_ids)


def _normalize_ids(agent_ids: Sequence[str]) -> list[str]:
    normalized: list[str] = []
    for value in agent_ids:
        normalized.extend(part.strip() for part in value.split(",") if part.strip())
    return sorted(set(normalized))


def _runtime_for(agent_id: str, assets: ConsumerRuntimeAssets) -> RuntimeAgentDefinition:
    for runtime in assets.runtime.agents:
        if runtime.agent_id == agent_id:
            return runtime
    raise GenerationError(f"Missing runtime definition for {agent_id!r}.")


def _toolbox_name(agent_id: str) -> str:
    return f"{agent_id}-knowledge"


def _uses_sap(runtime: RuntimeAgentDefinition) -> bool:
    return any(tool.descriptor.backend is ToolBackend.SAP for tool in runtime.tools)


def _requirements(descriptor: AgentDescriptor) -> str:
    requirements = _GENERATED_REQUIREMENTS
    if descriptor.implementation.framework == "PydanticAI":
        requirements += f"{_PYDANTIC_AI_REQUIREMENT}\n"
    return requirements


def _framework_tags(descriptor: AgentDescriptor) -> list[str]:
    common = [
        "Agent IQ",
        "AI Agent Hosting",
        "Responses Protocol",
        "A2A",
        "Incoming A2A",
        "Foundry IQ",
        "Python",
    ]
    if descriptor.implementation.framework == "LangGraph":
        return [*common, "LangGraph", "LangChain"]
    if descriptor.implementation.framework == "PydanticAI":
        return [*common, "PydanticAI", "LangGraph Host Adapter"]
    raise GenerationError(f"Unsupported hosted framework {descriptor.implementation.framework!r}.")


def _connection_name(agent_id: str) -> str:
    return f"{agent_id}-kb-mcp"


def _reject_source_output(output_root: Path) -> None:
    source_root = (_REPOSITORY_ROOT / "src").resolve()
    if output_root == source_root or source_root in output_root.parents:
        raise GenerationError("Generated deployments must be written outside the source tree.")


def _build_hash(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name, content in sorted(files.items()):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("utf-8")


def _yaml_text(payload: dict[str, Any]) -> str:
    return yaml.safe_dump(
        payload,
        sort_keys=False,
        allow_unicode=False,
        width=100,
    )


def _load_json_mapping(path: Path) -> dict[str, Any]:
    try:
        loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"Cannot load generated JSON {path}: {exc}") from exc
    return _mapping(loaded, str(path))


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        loaded: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValidationError(f"Cannot load generated YAML {path}: {exc}") from exc
    return _mapping(loaded, str(path))


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{label} must be a mapping.")
    return cast(dict[str, Any], value)


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValidationError(f"{label} must be a list.")
    return value


def _selection_arguments(parser: argparse.ArgumentParser) -> None:
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all", action="store_true", help="Select all 20 consumer agents.")
    selection.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="AGENT_IDS",
        help="Select one ID or comma-separated IDs; repeat as needed.",
    )


def _ids_from_arguments(arguments: argparse.Namespace) -> Sequence[str] | None:
    return None if bool(arguments.all) else cast(list[str], arguments.only)


def cli_generate(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate current Foundry hosted-agent projects for the consumer fleet.",
    )
    _selection_arguments(parser)
    parser.add_argument("--out", type=Path, default=_DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--model-version", default=DEFAULT_MODEL_VERSION)
    parser.add_argument("--model-sku", default=DEFAULT_MODEL_SKU)
    parser.add_argument("--model-capacity", type=int, default=DEFAULT_MODEL_CAPACITY)
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args(argv)
    artifacts = generate_consumer_deployments(
        arguments.out,
        agent_ids=_ids_from_arguments(arguments),
        options=GenerationOptions(
            model=arguments.model,
            model_version=arguments.model_version,
            model_sku=arguments.model_sku,
            model_capacity=arguments.model_capacity,
        ),
        dry_run=arguments.dry_run,
    )
    for artifact in artifacts:
        action = "PLAN" if arguments.dry_run else "GENERATED"
        print(
            f"{action} {artifact.agent_id} files={artifact.file_count} "
            f"sha256={artifact.build_hash} path={artifact.path}"
        )
    return 0


def cli_validate(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate current Foundry hosted-agent projects for the consumer fleet.",
    )
    _selection_arguments(parser)
    parser.add_argument("--out", type=Path, default=_DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--skip-graph-smoke", action="store_true")
    arguments = parser.parse_args(argv)
    artifacts = validate_consumer_deployments(
        arguments.out,
        agent_ids=_ids_from_arguments(arguments),
        graph_smoke=not arguments.skip_graph_smoke,
    )
    for artifact in artifacts:
        print(f"VALID {artifact.agent_id} files={artifact.file_count} sha256={artifact.build_hash}")
    print(f"Foundry deployment validation: PASS ({len(artifacts)} agents)")
    return 0
