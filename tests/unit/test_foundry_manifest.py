"""Current Foundry deployment generator tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from agent_iq.catalog import load_consumer_runtime_assets
from agent_iq.deployments import foundry_consumer
from agent_iq.deployments.foundry_consumer import (
    GENERATED_MARKER,
    GenerationError,
    GenerationOptions,
    ValidationError,
    generate_consumer_deployments,
    validate_consumer_deployments,
)


def generated_root(tmp_path: Path) -> Path:
    root = tmp_path / "generated"
    generate_consumer_deployments(root, agent_ids=["consumer-formulation-rd"])
    return root


def test_generate_and_validate_current_hero_artifact(tmp_path: Path) -> None:
    root = generated_root(tmp_path)
    artifact = root / "consumer-formulation-rd"

    validated = validate_consumer_deployments(
        root,
        agent_ids=["consumer-formulation-rd"],
    )
    azure = yaml.safe_load((artifact / "azure.yaml").read_text(encoding="utf-8"))
    service = azure["services"]["consumer-formulation-rd"]

    assert len(validated) == 1
    assert validated[0].file_count == 55
    assert service["protocols"] == [{"protocol": "responses", "version": "2.0.0"}]
    assert service["agentEndpoint"]["protocols"] == ["responses", "a2a"]
    assert service["agentCard"]["version"] == "1.0.0"
    assert "capabilities" not in service["agentCard"]
    assert "AGENT_IQ_SAP_MODE" not in {item["name"] for item in service["environmentVariables"]}
    assert not (artifact / "agent.yaml").exists()
    assert not (artifact / "agent.manifest.yaml").exists()
    assert not list(artifact.rglob("__pycache__"))
    assert (
        artifact / "src" / "consumer-formulation-rd" / "agent_iq" / "connectors" / "entra.py"
    ).is_file()
    requirements = (artifact / "src" / "consumer-formulation-rd" / "requirements.txt").read_text(
        encoding="utf-8"
    )
    provision_script = (artifact / "scripts" / "provision_toolbox.ps1").read_text(encoding="utf-8")
    assert "aiohttp==3.14.1" in requirements
    assert "langchain-mcp-adapters==0.3.0" in requirements
    assert "mcp==1.28.1" in requirements
    assert "azure-search-documents" not in requirements
    assert "--force" in provision_script
    assert "toolbox show consumer-formulation-rd-knowledge" in provision_script
    assert "${KB_MCP_ENDPOINT}" in provision_script


def test_employee_artifact_vendors_its_source_bound_contract(tmp_path: Path) -> None:
    root = tmp_path / "generated"
    generate_consumer_deployments(root, agent_ids=["employee-it-helpdesk"])
    validated = validate_consumer_deployments(root, agent_ids=["employee-it-helpdesk"])
    contract = (
        root
        / "employee-it-helpdesk"
        / "src"
        / "employee-it-helpdesk"
        / "agent_iq"
        / "data"
        / "consumer"
        / "corpora"
        / "employee-it-helpdesk.jsonl"
    )

    assert validated[0].file_count == 56
    assert contract.is_file()
    assert "VPN_RECOVERY_JSON_START" in contract.read_text(encoding="utf-8")


def test_sap_environment_is_derived_from_runtime_tool_metadata(tmp_path: Path) -> None:
    root = tmp_path / "generated"
    generate_consumer_deployments(root, agent_ids=["consumer-demand-forecasting"])
    validated = validate_consumer_deployments(
        root,
        agent_ids=["consumer-demand-forecasting"],
    )
    artifact = root / "consumer-demand-forecasting"
    azure = yaml.safe_load((artifact / "azure.yaml").read_text(encoding="utf-8"))
    environment = {
        item["name"]: item["value"]
        for item in azure["services"]["consumer-demand-forecasting"]["environmentVariables"]
    }

    assert validated[0].file_count == 56
    assert environment["AGENT_IQ_AZURE_AI_ACCOUNT"] == "${AGENT_IQ_AZURE_AI_ACCOUNT}"
    assert environment["AGENT_IQ_AZURE_AI_PROJECT"] == "${AGENT_IQ_AZURE_AI_PROJECT}"
    assert environment["AGENT_IQ_SAP_MODE"] == "fixture"
    assert environment["AGENT_IQ_ARTIFACT_PUBLISH_ENDPOINT"] == (
        "${AGENT_IQ_ARTIFACT_PUBLISH_ENDPOINT}"
    )
    assert environment["AGENT_IQ_ARTIFACT_COMPANION_APPLICATION_ID"] == (
        "${AGENT_IQ_ARTIFACT_COMPANION_APPLICATION_ID}"
    )
    assert environment["AGENT_IQ_ARTIFACT_COMPANION_TENANT_ID"] == (
        "${AGENT_IQ_ARTIFACT_COMPANION_TENANT_ID}"
    )
    service = azure["services"]["consumer-demand-forecasting"]
    assert "PydanticAI" in service["metadata"]["tags"]
    assert "LangGraph" not in service["metadata"]["tags"]
    env_example = (artifact / ".env.example").read_text(encoding="utf-8")
    assert "AGENT_IQ_AZURE_AI_ACCOUNT=<azure-ai-account>" in env_example
    assert "AGENT_IQ_AZURE_AI_PROJECT=<foundry-project>" in env_example
    assert "AGENT_IQ_SAP_MODE=fixture" in env_example
    assert "AGENT_IQ_ARTIFACT_PUBLISH_ENDPOINT=" in env_example
    assert "AGENT_IQ_ARTIFACT_COMPANION_APPLICATION_ID=" in env_example
    assert "AGENT_IQ_ARTIFACT_COMPANION_TENANT_ID=" in env_example
    assert "AGENT_IQ_ARTIFACT_TOKEN_SCOPE" not in env_example
    assert "NOT LIVE SAP" in env_example
    requirements = (
        artifact / "src" / "consumer-demand-forecasting" / "requirements.txt"
    ).read_text(encoding="utf-8")
    assert "pydantic-ai-slim[openai]==2.9.0" in requirements
    main = (artifact / "src" / "consumer-demand-forecasting" / "main.py").read_text(
        encoding="utf-8"
    )
    assert "build_hosted_server(EXPECTED_AGENT_ID)" in main
    readme = (artifact / "README.md").read_text(encoding="utf-8")
    assert "PydanticAI Agent with strict input/output models" in readme


def test_generation_is_deterministic_and_dry_run_writes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "generated"
    first = generate_consumer_deployments(root, agent_ids=["consumer-formulation-rd"])[0]
    second = generate_consumer_deployments(root, agent_ids=["consumer-formulation-rd"])[0]
    dry_root = tmp_path / "dry"
    planned = generate_consumer_deployments(
        dry_root,
        agent_ids=["consumer-formulation-rd"],
        dry_run=True,
    )[0]

    assert first.build_hash == second.build_hash == planned.build_hash
    assert planned.written is False
    assert not dry_root.exists()


def test_generated_directory_replace_retries_transient_windows_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    original_replace = Path.replace
    attempts = 0

    def transient_replace(path: Path, destination: Path) -> Path:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError("transient scanner lock")
        return original_replace(path, destination)

    monkeypatch.setattr(Path, "replace", transient_replace)
    monkeypatch.setattr("agent_iq.deployments.foundry_consumer.time.sleep", lambda _: None)

    foundry_consumer._replace_directory(source, target)

    assert attempts == 2
    assert target.is_dir()


def test_all_agents_can_be_planned_from_canonical_assets(tmp_path: Path) -> None:
    artifacts = generate_consumer_deployments(tmp_path / "all", dry_run=True)

    assert len(artifacts) == 24
    assert {artifact.agent_id for artifact in artifacts} == {
        agent.agent_id for agent in load_consumer_runtime_assets().catalog.agents
    }


def test_selection_options_and_output_safety(tmp_path: Path) -> None:
    with pytest.raises(GenerationError, match="Unknown"):
        generate_consumer_deployments(
            tmp_path,
            agent_ids=["consumer-missing"],
            dry_run=True,
        )
    with pytest.raises(GenerationError, match="At least one"):
        generate_consumer_deployments(tmp_path, agent_ids=[], dry_run=True)
    with pytest.raises(GenerationError, match="outside the source tree"):
        generate_consumer_deployments(
            Path(foundry_consumer.__file__).resolve().parents[2],
            agent_ids=["consumer-formulation-rd"],
            dry_run=True,
        )
    with pytest.raises(GenerationError, match="Invalid model"):
        generate_consumer_deployments(
            tmp_path,
            agent_ids=["consumer-formulation-rd"],
            options=GenerationOptions(model="not valid"),
            dry_run=True,
        )
    with pytest.raises(GenerationError, match="capacity"):
        generate_consumer_deployments(
            tmp_path,
            agent_ids=["consumer-formulation-rd"],
            options=GenerationOptions(model_capacity=0),
            dry_run=True,
        )


def test_generator_refuses_to_replace_unmarked_content(tmp_path: Path) -> None:
    target = tmp_path / "consumer-formulation-rd"
    target.mkdir()
    (target / "owned.txt").write_text("user content", encoding="utf-8")

    with pytest.raises(GenerationError, match="unmarked"):
        generate_consumer_deployments(
            tmp_path,
            agent_ids=["consumer-formulation-rd"],
        )

    assert (target / "owned.txt").read_text(encoding="utf-8") == "user content"


def test_integrity_tampering_is_detected(tmp_path: Path) -> None:
    root = generated_root(tmp_path)
    artifact = root / "consumer-formulation-rd"
    (artifact / "agent-card.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="build hash mismatch"):
        validate_consumer_deployments(
            root,
            agent_ids=["consumer-formulation-rd"],
            graph_smoke=False,
        )


def test_semantic_validators_reject_stale_preview_shapes(tmp_path: Path) -> None:
    root = generated_root(tmp_path)
    artifact = root / "consumer-formulation-rd"
    descriptor = next(
        agent
        for agent in load_consumer_runtime_assets().catalog.agents
        if agent.agent_id == "consumer-formulation-rd"
    )

    azure_path = artifact / "azure.yaml"
    azure = yaml.safe_load(azure_path.read_text(encoding="utf-8"))
    azure["services"]["consumer-formulation-rd"]["protocols"][0]["version"] = "1.0.0"
    azure_path.write_text(yaml.safe_dump(azure, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValidationError, match=r"Responses 2\.0\.0"):
        foundry_consumer._validate_azure_yaml(azure_path, descriptor)

    toolbox_path = artifact / "toolbox.yaml"
    toolbox = yaml.safe_load(toolbox_path.read_text(encoding="utf-8"))
    toolbox["tools"][0]["require_approval"] = "always"
    toolbox_path.write_text(yaml.safe_dump(toolbox, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValidationError, match="toolbox drifted"):
        foundry_consumer._validate_toolbox(
            toolbox_path,
            descriptor,
            load_consumer_runtime_assets(),
        )


@pytest.mark.parametrize(
    ("name", "text", "match"),
    [
        ("legacy.txt", "pine" + "apple", "legacy public name"),
        ("old-a2a.txt", "A2A protocol version 0.2", "unsupported A2A"),
        ("coordinate.txt", "tenant 11111111-1111-1111-1111-111111111111", "coordinate"),
        (
            "token.txt",
            "api_" + 'key = "hardcoded-placeholder"',
            "literal credential",
        ),
        (
            "retired.txt",
            "azure-ai-agentserver-langgraph",
            "retired preview package",
        ),
        ("direct.txt", "KnowledgeBaseRetrievalClient", "direct Search retrieval"),
        ("static.txt", "FOUNDRY_IQ_TOKEN", "static Foundry IQ token"),
    ],
)
def test_forbidden_generated_content_is_rejected(
    name: str,
    text: str,
    match: str,
) -> None:
    with pytest.raises(ValidationError, match=match):
        foundry_consumer._validate_text_content("consumer-test", {name: text.encode()})


def test_structure_and_parse_errors_are_explicit(tmp_path: Path) -> None:
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{", encoding="utf-8")
    with pytest.raises(ValidationError, match="Cannot load generated JSON"):
        foundry_consumer._load_json_mapping(bad_json)

    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("[", encoding="utf-8")
    with pytest.raises(ValidationError, match="Cannot load generated YAML"):
        foundry_consumer._load_yaml_mapping(bad_yaml)

    with pytest.raises(ValidationError, match="must be a mapping"):
        foundry_consumer._mapping([], "value")
    with pytest.raises(ValidationError, match="must be a list"):
        foundry_consumer._list({}, "value")


def test_invalid_python_and_missing_artifact_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="Missing generated artifact"):
        validate_consumer_deployments(
            tmp_path,
            agent_ids=["consumer-formulation-rd"],
            graph_smoke=False,
        )

    target = tmp_path / "bad"
    target.mkdir()
    (target / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="Python syntax error"):
        foundry_consumer._compile_python(target, "consumer-test")


def test_vendored_source_drift_is_rejected(tmp_path: Path) -> None:
    root = generated_root(tmp_path)
    descriptor = next(
        agent
        for agent in load_consumer_runtime_assets().catalog.agents
        if agent.agent_id == "consumer-formulation-rd"
    )
    config = root / descriptor.agent_id / "src" / descriptor.agent_id / "agent_iq" / "config.py"
    config.write_text("# drifted\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="vendored source drifted"):
        foundry_consumer._validate_vendored_source(root / descriptor.agent_id, descriptor)


def test_graph_smoke_batches_frameworks_in_one_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    selected = {"consumer-demand-forecasting", "consumer-formulation-rd"}
    descriptors = tuple(
        agent
        for agent in load_consumer_runtime_assets().catalog.agents
        if agent.agent_id in selected
    )
    captured: dict[str, Any] = {}

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args[0], 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    foundry_consumer._smoke_generated_graphs(tmp_path, descriptors)

    command = captured["args"][0]
    kwargs = captured["kwargs"]
    assert command[:2] == [sys.executable, "-c"]
    assert all(agent_id in command[2] for agent_id in selected)
    assert kwargs["cwd"] == (
        tmp_path / "consumer-demand-forecasting" / "src" / "consumer-demand-forecasting"
    )
    assert kwargs["timeout"] == foundry_consumer._GRAPH_SMOKE_TIMEOUT_SECONDS


def test_graph_smoke_timeout_reports_batch_size(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    descriptors = tuple(load_consumer_runtime_assets().catalog.agents[:2])

    def timeout_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout_run)

    with pytest.raises(ValidationError, match="for 2 agents"):
        foundry_consumer._smoke_generated_graphs(tmp_path, descriptors)


def test_cli_supports_only_dry_run_and_validation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "cli"
    assert (
        foundry_consumer.cli_generate(
            [
                "--only",
                "consumer-formulation-rd",
                "--out",
                str(root),
                "--dry-run",
            ]
        )
        == 0
    )
    assert "PLAN consumer-formulation-rd" in capsys.readouterr().out

    generate_consumer_deployments(root, agent_ids=["consumer-formulation-rd"])
    assert (
        foundry_consumer.cli_validate(
            [
                "--only",
                "consumer-formulation-rd",
                "--out",
                str(root),
                "--skip-graph-smoke",
            ]
        )
        == 0
    )
    assert "Foundry deployment validation: PASS" in capsys.readouterr().out


def test_build_marker_contains_no_environment_coordinates(tmp_path: Path) -> None:
    root = generated_root(tmp_path)
    marker_path = root / "consumer-formulation-rd" / GENERATED_MARKER
    marker: dict[str, Any] = json.loads(marker_path.read_text(encoding="utf-8"))

    assert marker["responsesVersion"] == "2.0.0"
    assert marker["a2aCardVersion"] == "1.0.0"
    assert marker["knowledgeBindingId"] == "consumer-product-innovation-kb"
    assert "tenant" not in marker
