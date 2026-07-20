from __future__ import annotations

import argparse
import asyncio
import copy
import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from scripts import run_foundry_evidence_canary as evidence_canary
from scripts import run_live_orchestration_canary as orchestration_canary


def _evidence_payload(phase: str, run: int) -> dict[str, object]:
    return {
        "schemaVersion": "1.0",
        "phase": phase,
        "status": "pass" if run % 2 else "fail",
        "prompt": {
            "length": 10,
            "hash": f"sha256:{'a' * 64}",
        },
    }


def _sensitive_evidence_payload() -> tuple[dict[str, object], tuple[str, ...]]:
    sentinels = (
        "sentinel-source-id",
        "sentinel-local-reference",
        "sentinel-source-title",
        "sentinel-user-prompt",
        "sentinel-agent-answer",
        "sentinel-source-markdown",
        "sentinel-context-id",
        "sentinel-tool-arguments",
        "sentinel-credential",
    )
    (
        source_id,
        local_reference_id,
        title,
        prompt,
        answer,
        source_markdown,
        context_id,
        tool_arguments,
        credential,
    ) = sentinels
    payload: dict[str, object] = {
        "schemaVersion": "1.0",
        "status": "pass",
        "phase": "final",
        "recordedAt": "2026-07-12T12:00:00+00:00",
        "transportDecision": {
            "selected": "companion",
            "reason": "Structured evidence is correlated through Agent IQ.",
            "officialSource": f"https://user:{credential}@example.com/source",
            "githubSourceRevision": "a" * 40,
        },
        "agent": {
            "agentId": "consumer-formulation-rd",
            "agentVersion": "7",
            "protocolVersion": "1.0",
            "cardProtocolVersion": "1.0",
            "catalogEvidenceExtension": "companion",
            "servedEvidenceExtension": None,
            "servedEvidenceExtensionPreserved": False,
            "platformOmittedFields": ["capabilities.extensions"],
            "contextId": context_id,
        },
        "nativeA2A": {
            "event_count": 2,
            "payload_kinds": ["message"],
            "text_part_count": 1,
            "data_part_count": 0,
            "artifact_count": 0,
            "metadata_keys": [],
            "data_keys": [],
            "preservesStructuredParts": False,
            "answerLength": len(answer),
            "answerHash": evidence_canary._digest(answer),
            "answerHasReferenceMarker": True,
            "answer": answer,
        },
        "responses": {
            "output_item_types": ["message", "structured_outputs"],
            "structured_output_count": 1,
            "structured_output_keys": ["records"],
            "completed": True,
            "toolArguments": tool_arguments,
        },
        "evidence": {
            "delivery": "companion",
            "recordCount": 1,
            "records": [
                {
                    "sourceId": source_id,
                    "localReferenceId": local_reference_id,
                    "title": title,
                    "excerptLength": len(source_markdown),
                    "contentHash": evidence_canary._digest(source_markdown),
                    "hasUri": True,
                    "excerpt": source_markdown,
                    "sourceMarkdown": source_markdown,
                    "sourceUri": f"https://example.com/{source_id}",
                }
            ],
            "unknownSourceRejected": True,
            "isolation": {
                "caller": True,
                "tenant": True,
                "invocation": True,
                "correlation": True,
                "contextId": context_id,
            },
        },
        "prompt": {
            "length": len(prompt),
            "hash": evidence_canary._digest(prompt),
            "text": prompt,
        },
        "gates": {
            "evidenceRecordCount": True,
            "requiredStableSources": True,
            "companionSelected": True,
            context_id: True,
        },
        "answer": answer,
        "sourceMarkdown": source_markdown,
        "contextId": context_id,
        "tool": {"arguments": tool_arguments},
        "credentials": {"token": credential},
    }
    return payload, sentinels


@pytest.mark.parametrize(
    "extra_arguments",
    [
        [
            "--project-endpoint",
            "https://other-account.services.ai.azure.com/api/projects/project-test",
            "--account-name",
            "account-test",
            "--project-name",
            "project-test",
        ],
        ["--search-service", "Invalid_Search"],
    ],
)
def test_orchestration_target_binding_precedes_credentials(
    monkeypatch: pytest.MonkeyPatch,
    extra_arguments: list[str],
) -> None:
    credential_calls = 0

    def forbidden_credential() -> object:
        nonlocal credential_calls
        credential_calls += 1
        raise AssertionError("Credential constructed before target binding.")

    monkeypatch.setattr(
        orchestration_canary,
        "DefaultAzureBearerTokenProvider",
        forbidden_credential,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_live_orchestration_canary.py", *extra_arguments],
    )
    with pytest.raises(ValueError):
        orchestration_canary.main()
    assert credential_calls == 0


def test_foundry_evidence_writer_keeps_phase_and_final_latest_pointers(
    tmp_path: Path,
) -> None:
    preview = evidence_canary._write_report(_evidence_payload("preview", 1), tmp_path)
    final = evidence_canary._write_report(_evidence_payload("final", 2), tmp_path)
    later_preview = evidence_canary._write_report(_evidence_payload("preview", 3), tmp_path)

    assert preview != final
    assert final != later_preview
    assert json.loads((tmp_path / "latest-preview.json").read_text())["status"] == "pass"
    assert json.loads((tmp_path / "latest-final.json").read_text())["status"] == "fail"
    assert json.loads((tmp_path / "latest.json").read_text())["status"] == "fail"
    assert not list(tmp_path.rglob("*.tmp"))


def test_foundry_evidence_writer_retains_only_exact_phase_runs(
    tmp_path: Path,
) -> None:
    unrelated = tmp_path / "unrelated.json"
    unrelated.write_text("keep", encoding="utf-8")
    unrelated_directory = tmp_path / "foundry-evidence-final-not-a-run"
    unrelated_directory.mkdir()
    (unrelated_directory / "marker.txt").write_text("keep", encoding="utf-8")
    near_match = tmp_path / "foundry-evidence-final-not-a-run.json"
    near_match.write_text("keep", encoding="utf-8")

    paths = [
        evidence_canary._write_report(_evidence_payload("final", index), tmp_path)
        for index in range(evidence_canary.MAX_RUN_REPORTS + 3)
    ]

    pattern = evidence_canary._run_name_pattern("final")
    managed = [
        path for path in tmp_path.iterdir() if path.is_file() and pattern.fullmatch(path.name)
    ]
    assert len(paths) == len({path.name for path in paths})
    assert len(managed) == evidence_canary.MAX_RUN_REPORTS
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert near_match.read_text(encoding="utf-8") == "keep"
    assert (unrelated_directory / "marker.txt").read_text(encoding="utf-8") == "keep"
    assert not list(tmp_path.rglob("*.tmp"))


def test_live_orchestration_writer_is_collision_safe_bounded_and_isolated(
    tmp_path: Path,
) -> None:
    unrelated = tmp_path / "unrelated.json"
    unrelated.write_text("keep", encoding="utf-8")
    unrelated_directory = tmp_path / f"{orchestration_canary.RUN_REPORT_PREFIX}not-a-run"
    unrelated_directory.mkdir()
    (unrelated_directory / "marker.txt").write_text("keep", encoding="utf-8")
    near_match = tmp_path / f"{orchestration_canary.RUN_REPORT_PREFIX}not-a-run.json"
    near_match.write_text("keep", encoding="utf-8")

    paths = [
        orchestration_canary._write_report(
            {"schemaVersion": "1.0", "run": index},
            tmp_path,
        )
        for index in range(orchestration_canary.MAX_RUN_REPORTS + 3)
    ]

    managed = [
        path
        for path in tmp_path.iterdir()
        if path.is_file() and orchestration_canary._RUN_REPORT_NAME.fullmatch(path.name)
    ]
    assert len(paths) == len({path.name for path in paths})
    assert len(managed) == orchestration_canary.MAX_RUN_REPORTS
    assert (tmp_path / "latest.json").read_text(encoding="utf-8") in {
        path.read_text(encoding="utf-8") for path in managed
    }
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert near_match.read_text(encoding="utf-8") == "keep"
    assert (unrelated_directory / "marker.txt").read_text(encoding="utf-8") == "keep"
    assert not list(tmp_path.rglob("*.tmp"))


@pytest.mark.parametrize(
    ("replace_path", "writer", "payload"),
    (
        (
            "scripts.run_foundry_evidence_canary.os.replace",
            evidence_canary._write_report,
            _evidence_payload("final", 1),
        ),
        (
            "scripts.run_live_orchestration_canary.os.replace",
            orchestration_canary._write_report,
            {"schemaVersion": "1.0", "run": 1},
        ),
    ),
)
def test_canary_writers_clean_temp_on_atomic_failure(
    replace_path: str,
    writer: Callable[[dict[str, object], Path], Path],
    payload: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_replace(source: object, destination: object) -> None:
        del source, destination
        raise OSError("simulated replace failure")

    monkeypatch.setattr(replace_path, fail_replace)

    with pytest.raises(OSError, match="simulated"):
        writer(payload, tmp_path)

    assert not list(tmp_path.rglob("*.tmp"))


def test_live_orchestration_writer_rejects_raw_content_before_persistence(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError):
        orchestration_canary._write_report(
            {"schemaVersion": "1.0", "arguments": {"query": "do not persist"}},
            tmp_path,
        )

    assert not list(tmp_path.iterdir())


def test_foundry_evidence_writer_persists_only_allowlisted_hashed_evidence(
    tmp_path: Path,
) -> None:
    payload, sentinels = _sensitive_evidence_payload()
    original = copy.deepcopy(payload)

    path = evidence_canary._write_report(payload, tmp_path)

    assert payload == original
    persisted = json.loads(path.read_text(encoding="utf-8"))
    rendered = json.dumps(persisted, sort_keys=True)
    for sentinel in sentinels:
        assert sentinel not in rendered
    assert set(persisted) == {
        "agent",
        "evidence",
        "gates",
        "nativeA2A",
        "persistence",
        "phase",
        "prompt",
        "recordedAt",
        "responses",
        "schemaVersion",
        "status",
        "transportDecision",
    }
    assert "officialSource" not in persisted["transportDecision"]
    assert persisted["status"] == "pass"
    assert persisted["evidence"]["recordCount"] == 1
    assert len(persisted["evidence"]["records"]) == 1
    record = persisted["evidence"]["records"][0]
    assert record == {
        "contentHash": evidence_canary._digest("sentinel-source-markdown"),
        "excerptLength": len("sentinel-source-markdown"),
        "hasUri": True,
        "localReferenceIdLength": len("sentinel-local-reference"),
        "localReferenceIdSha256": evidence_canary._digest("sentinel-local-reference"),
        "sourceIdLength": len("sentinel-source-id"),
        "sourceIdSha256": evidence_canary._digest("sentinel-source-id"),
        "titleLength": len("sentinel-source-title"),
        "titleSha256": evidence_canary._digest("sentinel-source-title"),
    }
    assert persisted["evidence"]["isolation"] == {
        "caller": True,
        "correlation": True,
        "invocation": True,
        "tenant": True,
    }
    assert persisted["gates"] == {
        "companionSelected": True,
        "evidenceRecordCount": True,
        "requiredStableSources": True,
    }
    assert persisted["persistence"]["contentSafe"] is True


def test_foundry_evidence_json_stdout_uses_same_content_safe_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload, sentinels = _sensitive_evidence_payload()

    async def fake_certify(arguments: object) -> dict[str, object]:
        del arguments
        return payload

    monkeypatch.setattr(evidence_canary, "certify", fake_certify)
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_foundry_evidence_canary.py",
            "--project-endpoint",
            "https://example.services.ai.azure.com/api/projects/test",
            "--account-name",
            "example",
            "--project-name",
            "test",
            "--search-service",
            "example-search",
            "--report-dir",
            str(tmp_path),
            "--json",
        ],
    )

    assert evidence_canary.main() == 0
    stdout = capsys.readouterr().out
    persisted = json.loads(stdout)
    assert persisted == evidence_canary._persisted_report(payload)
    for sentinel in sentinels:
        assert sentinel not in stdout


def test_foundry_evidence_persistence_revalidates_urls() -> None:
    payload, _ = _sensitive_evidence_payload()
    transport = payload["transportDecision"]
    assert isinstance(transport, dict)
    transport["officialSource"] = (
        "https://learn.microsoft.com/azure/foundry/agents/how-to/enable-agent-to-agent-endpoint"
    )

    safe = evidence_canary._persisted_report(payload)
    assert safe["transportDecision"]["officialSource"] == transport["officialSource"]

    transport["officialSource"] = "https://127.0.0.1/private"
    unsafe = evidence_canary._persisted_report(payload)
    assert "officialSource" not in unsafe["transportDecision"]


def test_foundry_evidence_target_mismatch_precedes_credentials_and_cloud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential_calls = 0

    def fail_credential() -> object:
        nonlocal credential_calls
        credential_calls += 1
        raise AssertionError("Credentials must not be constructed for a mismatched target.")

    monkeypatch.setattr(
        evidence_canary,
        "DefaultAzureBearerTokenProvider",
        fail_credential,
    )

    with pytest.raises(ValueError, match="does not match"):
        asyncio.run(
            evidence_canary.certify(
                argparse.Namespace(
                    project_endpoint=(
                        "https://other.services.ai.azure.com/api/projects/project-test"
                    ),
                    account_name="account-test",
                    project_name="project-test",
                    search_service="search-test",
                )
            )
        )

    assert credential_calls == 0
