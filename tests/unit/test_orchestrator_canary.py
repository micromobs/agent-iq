from __future__ import annotations

import argparse
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from a2a.types import AgentCard, AgentInterface
from scripts import certify_orchestrator_canary as orchestrator_certifier
from scripts.certify_orchestrator_canary import (
    EXPECTED_AGENT,
    EXPECTED_DEPLOYMENT_OWNERSHIP_TAGS,
    MAX_DEPLOYMENT_REPORT_AGE,
    MAX_RUN_CERTIFICATES,
    RUN_CERTIFICATE_PREFIX,
    _deployment_evidence,
    _deployment_report_path_argument,
    _transport_parity,
    _trusted_a2a_card,
    _write_report,
)

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def test_orchestrator_report_names_are_unique_at_the_same_instant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FrozenDateTime:
        @classmethod
        def now(cls, tz: object) -> datetime:
            del tz
            return NOW

    monkeypatch.setattr(orchestrator_certifier, "datetime", FrozenDateTime)

    report = {"schemaVersion": "1.0", "baseUrl": "https://example.invalid/"}
    first = _write_report(report, tmp_path)
    second = _write_report(report, tmp_path)

    assert first != second
    assert first.name.startswith(RUN_CERTIFICATE_PREFIX)
    assert second.name.startswith(RUN_CERTIFICATE_PREFIX)
    assert json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))["baseUrl"] == (
        "https://example.invalid"
    )
    assert not any(path.suffix == ".tmp" for path in tmp_path.iterdir())


def test_orchestrator_report_writes_are_concurrent_safe(tmp_path: Path) -> None:
    def write(index: int) -> Path:
        return _write_report({"schemaVersion": "1.0", "run": index}, tmp_path)

    with ThreadPoolExecutor(max_workers=8) as executor:
        paths = list(executor.map(write, range(8)))

    assert len({path.name for path in paths}) == len(paths)
    assert all(
        json.loads(path.read_text(encoding="utf-8"))["schemaVersion"] == "1.0" for path in paths
    )
    latest = (tmp_path / "latest.json").read_text(encoding="utf-8")
    assert latest in {path.read_text(encoding="utf-8") for path in paths}
    assert not any(path.suffix == ".tmp" for path in tmp_path.iterdir())


def test_transport_parity_rejects_consistently_wrong_agent() -> None:
    normalized = {
        name: {"selected": ["consumer-brand-to-brand-compare"], "answer": "same"}
        for name in ("http_sse", "mcp", "a2a")
    }

    assert not _transport_parity(
        normalized,
        (("consumer-brand-to-brand-compare",),) * 4,
    )


def test_transport_parity_requires_expected_agent_on_every_route() -> None:
    normalized = {
        name: {"selected": [EXPECTED_AGENT], "answer": "same"}
        for name in ("http_sse", "mcp", "a2a")
    }

    assert not _transport_parity(
        normalized,
        (
            (EXPECTED_AGENT,),
            (EXPECTED_AGENT,),
            (EXPECTED_AGENT,),
            ("consumer-demand-forecasting",),
        ),
    )


@pytest.mark.parametrize(
    ("method", "path"),
    (
        ("GET", "/healthz"),
        ("GET", "/readyz"),
        ("GET", "/v1/capabilities"),
        ("POST", "/v1/orchestrate"),
    ),
)
def test_transport_certification_rejects_redirects_without_following(
    method: str,
    path: str,
) -> None:
    requests: list[str] = []

    def redirect(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(302, headers={"Location": "/redirect-target"})

    async def request() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(redirect),
            follow_redirects=True,
        ) as client:
            await orchestrator_certifier._request_without_redirects(
                client,
                method,
                f"https://example.invalid{path}",
                json={} if method == "POST" else None,
            )

    with pytest.raises(httpx.HTTPStatusError, match="Redirects are forbidden"):
        asyncio.run(request())
    assert requests == [path]


@pytest.mark.parametrize("status_code", (300, 304, 399))
def test_transport_certification_fails_closed_on_every_3xx_range(
    status_code: int,
) -> None:
    async def request() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(status_code, request=request)
            )
        ) as client:
            await orchestrator_certifier._request_without_redirects(
                client,
                "GET",
                "https://example.invalid/healthz",
            )

    with pytest.raises(httpx.HTTPStatusError, match="Redirects are forbidden"):
        asyncio.run(request())


def test_orchestrator_report_retention_preserves_unrelated_files(tmp_path: Path) -> None:
    unrelated = tmp_path / "unrelated.json"
    unrelated.write_text("keep me", encoding="ascii")
    other_certificate = tmp_path / "launch-command-center-20260711T120000000000Z.json"
    other_certificate.write_text("also keep me", encoding="ascii")

    for index in range(MAX_RUN_CERTIFICATES + 3):
        _write_report({"schemaVersion": "1.0", "run": index}, tmp_path)

    run_files = [
        path
        for path in tmp_path.iterdir()
        if path.is_file() and path.name.startswith(RUN_CERTIFICATE_PREFIX)
    ]
    assert len(run_files) == MAX_RUN_CERTIFICATES
    assert unrelated.read_text(encoding="ascii") == "keep me"
    assert other_certificate.read_text(encoding="ascii") == "also keep me"


@pytest.mark.parametrize(
    "base_url",
    (
        "https://user:password@example.invalid",  # pragma: allowlist secret
        "https://example.invalid?access_token=secret",
        "https://example.invalid/#secret",
    ),
)
def test_orchestrator_base_url_rejects_credentials_query_and_fragment(base_url: str) -> None:
    with pytest.raises(ValueError):
        orchestrator_certifier._validate_base_url(base_url)


def test_orchestrator_report_rejects_an_unsafe_endpoint_before_persistence(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError):
        _write_report(
            {
                "schemaVersion": "1.0",
                "baseUrl": "https://user:password@example.invalid",  # pragma: allowlist secret
            },
            tmp_path,
        )

    assert not any(tmp_path.iterdir())


@pytest.mark.parametrize(
    "interfaces",
    (
        (),
        (
            AgentInterface(
                url="https://example.invalid/a2a",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            ),
            AgentInterface(
                url="https://example.invalid/a2a",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            ),
        ),
        (
            AgentInterface(
                url="https://attacker.invalid/a2a",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            ),
        ),
        (
            AgentInterface(
                url="https://example.invalid:443/a2a",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            ),
        ),
        (
            AgentInterface(
                url="https://user@example.invalid/a2a",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            ),
        ),
        (
            AgentInterface(
                url="https://example.invalid/a2%61",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            ),
        ),
        (
            AgentInterface(
                url="https://example.invalid/a2a/alternate",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            ),
        ),
        (
            AgentInterface(
                url="https://example.invalid/a2a?target=https://attacker.invalid",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            ),
        ),
        (
            AgentInterface(
                url="https://example.invalid/a2a#alternate",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            ),
        ),
        (
            AgentInterface(
                url="https://example.invalid/a2a",
                protocol_binding="HTTP+JSON",
                protocol_version="1.0",
            ),
        ),
        (
            AgentInterface(
                url="https://example.invalid/a2a",
                protocol_binding="JSONRPC",
                protocol_version="0.3",
            ),
        ),
    ),
)
def test_malicious_a2a_cards_fail_before_client_creation(
    interfaces: tuple[AgentInterface, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory_calls: list[str] = []

    def forbidden_factory(*args: object, **kwargs: object) -> object:
        del args, kwargs
        factory_calls.append("created")
        raise AssertionError("ClientFactory must not receive an untrusted Agent Card.")

    monkeypatch.setattr(orchestrator_certifier, "ClientFactory", forbidden_factory)
    card = AgentCard(name="malicious", supported_interfaces=interfaces)
    http = httpx.AsyncClient()
    try:
        with pytest.raises(RuntimeError, match="Agent Card"):
            orchestrator_certifier._create_trusted_a2a_client(
                card,
                base_url="https://example.invalid",
                httpx_client=http,
            )
    finally:
        asyncio.run(http.aclose())

    assert factory_calls == []


def test_trusted_a2a_card_reconstructs_the_certified_interface() -> None:
    advertised = AgentCard(
        name="Agent IQ Coordinator",
        supported_interfaces=(
            AgentInterface(
                url="https://example.invalid/a2a",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
                tenant="untrusted-advertised-tenant",
            ),
        ),
    )

    trusted = _trusted_a2a_card(advertised, base_url="https://example.invalid")

    assert trusted is not advertised
    assert len(trusted.supported_interfaces) == 1
    assert trusted.supported_interfaces[0].url == "https://example.invalid/a2a"
    assert trusted.supported_interfaces[0].protocol_binding == "JSONRPC"
    assert trusted.supported_interfaces[0].protocol_version == "1.0"
    assert trusted.supported_interfaces[0].tenant == ""


APP_NAME = "agent-iq-orchestrator-canary"
RESOURCE_GROUP = "rg-agent-iq"
REVISION = f"{APP_NAME}--d12abc"
IMAGE_DIGEST = f"sha256:{'a' * 64}"
IMAGE = f"registry.example.invalid/agent-iq-orchestrator@{IMAGE_DIGEST}"


def _deployment_report(**overrides: object) -> dict[str, object]:
    report: dict[str, object] = {
        "schemaVersion": "1.0",
        "generatedAt": NOW.isoformat(),
        "status": "pass",
        "appName": APP_NAME,
        "resourceGroup": RESOURCE_GROUP,
        "fqdn": "https://example.invalid",
        "revision": REVISION,
        "image": IMAGE,
        "imageDigest": IMAGE_DIGEST,
        "ownershipTags": dict(EXPECTED_DEPLOYMENT_OWNERSHIP_TAGS),
        "ingress": ["mcp", "http_sse", "a2a"],
        "containerAppIngress": {
            "external": True,
            "targetPort": 8080,
            "transport": "auto",
        },
        "authentication": "anonymous_canary",
        "containerAppAuthConfig": "none",
        "applicationInsights": "agent-iq-orchestrator-ai",
        "minReplicas": 1,
        "maxReplicas": 1,
    }
    report.update(overrides)
    return report


def _current_container_app(
    *,
    revision: str = REVISION,
    fqdn: str = "example.invalid",
    image: str = IMAGE,
    tags: dict[str, str] | None = None,
    external: bool = True,
    target_port: int = 8080,
    transport: str = "Auto",
    anonymous_canary: str = "true",
    public_base_url: str = "https://example.invalid",
    min_replicas: int = 1,
    max_replicas: int = 1,
    active_revisions_mode: str = "Multiple",
    traffic: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "name": APP_NAME,
        "tags": dict(EXPECTED_DEPLOYMENT_OWNERSHIP_TAGS) if tags is None else tags,
        "properties": {
            "latestReadyRevisionName": revision,
            "configuration": {
                "activeRevisionsMode": active_revisions_mode,
                "ingress": {
                    "external": external,
                    "fqdn": fqdn,
                    "targetPort": target_port,
                    "transport": transport,
                    "traffic": (
                        [{"revisionName": revision, "weight": 100}] if traffic is None else traffic
                    ),
                },
            },
            "template": {
                "containers": [
                    {
                        "image": image,
                        "env": [
                            {
                                "name": "AGENT_IQ_ALLOW_ANONYMOUS_CONNECTOR",
                                "value": anonymous_canary,
                            },
                            {
                                "name": "AGENT_IQ_PUBLIC_BASE_URL",
                                "value": public_base_url,
                            },
                        ],
                    }
                ],
                "scale": {
                    "minReplicas": min_replicas,
                    "maxReplicas": max_replicas,
                },
            },
        },
    }


def _current_revision(
    *,
    revision: str = REVISION,
    image: str = IMAGE,
    active: bool = True,
    health_state: str = "Healthy",
    provisioning_state: str = "Provisioned",
    running_state: str = "Running",
    public_base_url: str = "https://example.invalid",
) -> dict[str, object]:
    return {
        "name": revision,
        "properties": {
            "active": active,
            "healthState": health_state,
            "provisioningState": provisioning_state,
            "runningState": running_state,
            "template": {
                "containers": [
                    {
                        "image": image,
                        "env": [
                            {
                                "name": "AGENT_IQ_PUBLIC_BASE_URL",
                                "value": public_base_url,
                            }
                        ],
                    }
                ]
            },
        },
    }


def _write_deployment_report(tmp_path: Path, report: dict[str, object]) -> Path:
    path = tmp_path / ("orchestrator-canary-run-20260711T1200000000000Z-" + "a" * 32 + ".json")
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def _deployment_evidence_for_test(
    path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    current: dict[str, object] | None = None,
    auth_config: dict[str, object] | None = None,
    revision_state: dict[str, object] | None = None,
    revision_query_fails: bool = False,
) -> dict[str, Any]:
    monkeypatch.setattr(
        orchestrator_certifier,
        "_query_current_container_app",
        lambda resource_group, app_name: (
            current if current is not None else _current_container_app()
        ),
    )
    monkeypatch.setattr(
        orchestrator_certifier,
        "_query_current_container_app_auth",
        lambda resource_group, app_name: {} if auth_config is None else auth_config,
    )
    if revision_query_fails:

        def query_revision(*args: object) -> dict[str, object]:
            raise RuntimeError("missing revision")
    else:

        def query_revision(*args: object) -> dict[str, object]:
            return _current_revision() if revision_state is None else revision_state

    monkeypatch.setattr(
        orchestrator_certifier,
        "_query_current_container_app_revision",
        query_revision,
    )
    return _deployment_evidence(
        path,
        base_url="https://example.invalid/",
        resource_group=RESOURCE_GROUP,
        app_name=APP_NAME,
        now=NOW,
    )


def test_deployment_evidence_requires_an_explicit_run_report_and_current_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_azure_call(*args: object, **kwargs: object) -> dict[str, object]:
        raise AssertionError("Azure must not be queried without a report.")

    monkeypatch.setattr(orchestrator_certifier, "_query_current_container_app", no_azure_call)
    result = _deployment_evidence(
        None,
        base_url="https://example.invalid",
        resource_group=RESOURCE_GROUP,
        app_name=APP_NAME,
        now=NOW,
    )

    assert result["valid"] is False
    assert "reportPath" in result["failures"]


@pytest.mark.parametrize("running_state", ("Running", "RunningAtMaxScale"))
def test_deployment_evidence_accepts_matching_run_report_and_current_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    running_state: str,
) -> None:
    path = _write_deployment_report(tmp_path, _deployment_report())
    result = _deployment_evidence_for_test(
        path,
        monkeypatch,
        revision_state=_current_revision(running_state=running_state),
    )

    assert result["valid"] is True
    assert result["checks"]["currentRevision"] is True
    assert result["checks"]["currentRevisionState"] is True
    assert result["checks"]["currentRevisionImageDigest"] is True
    assert result["checks"]["currentTraffic"] is True
    assert result["checks"]["currentIngressFqdn"] is True
    assert result["checks"]["currentImageDigest"] is True
    assert result["checks"]["currentOwnershipTags"] is True
    assert result["checks"]["currentIngressExternal"] is True
    assert result["checks"]["currentIngressTargetPort"] is True
    assert result["checks"]["currentIngressTransport"] is True
    assert result["checks"]["currentAuthentication"] is True
    assert result["checks"]["currentPublicBaseUrl"] is True
    assert result["checks"]["currentRevisionPublicBaseUrl"] is True
    assert result["checks"]["currentReplicaBounds"] is True


@pytest.mark.parametrize(
    "traffic",
    (
        [
            {"revisionName": REVISION, "weight": 50},
            {"revisionName": f"{APP_NAME}--old", "weight": 50},
        ],
        [
            {"revisionName": REVISION, "weight": 100},
            {"revisionName": f"{APP_NAME}--old", "weight": 0, "label": "old"},
        ],
        [
            {"revisionName": REVISION, "weight": 50},
            {"latestRevision": True, "weight": 50},
        ],
    ),
)
def test_deployment_evidence_rejects_split_old_or_ambiguous_traffic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    traffic: list[dict[str, object]],
) -> None:
    path = _write_deployment_report(tmp_path, _deployment_report())
    current = _current_container_app(active_revisions_mode="Multiple", traffic=traffic)

    result = _deployment_evidence_for_test(path, monkeypatch, current=current)

    assert result["valid"] is False
    assert "currentTraffic" in result["failures"]


def test_deployment_evidence_resolves_one_latest_revision_alias_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write_deployment_report(tmp_path, _deployment_report())
    current = _current_container_app(
        active_revisions_mode="Single",
        traffic=[{"latestRevision": True, "weight": 100}],
    )

    result = _deployment_evidence_for_test(path, monkeypatch, current=current)

    assert result["valid"] is True
    assert result["checks"]["currentTraffic"] is True


@pytest.mark.parametrize(
    ("revision_state", "query_fails", "expected_failure"),
    (
        (
            _current_revision(
                image=f"registry.example.invalid/agent-iq-orchestrator@sha256:{'b' * 64}"
            ),
            False,
            "currentRevisionImageDigest",
        ),
        (_current_revision(active=False), False, "currentRevisionState"),
        (
            {
                "name": REVISION,
                "properties": {
                    "active": True,
                    "healthState": "Healthy",
                    "provisioningState": "Provisioned",
                    "runningState": "Running",
                    "template": {"containers": [{"image": IMAGE}, {"image": IMAGE}]},
                },
            },
            False,
            "currentRevisionImageDigest",
        ),
        (
            _current_revision(public_base_url="https://drift.example.invalid"),
            False,
            "currentRevisionPublicBaseUrl",
        ),
        (
            _current_revision(public_base_url=""),
            False,
            "currentRevisionPublicBaseUrl",
        ),
        ({}, False, "currentRevisionState"),
        (None, True, "currentRevisionState"),
    ),
)
def test_deployment_evidence_requires_reported_revision_ready_with_exact_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    revision_state: dict[str, object] | None,
    query_fails: bool,
    expected_failure: str,
) -> None:
    path = _write_deployment_report(tmp_path, _deployment_report())

    result = _deployment_evidence_for_test(
        path,
        monkeypatch,
        revision_state=revision_state,
        revision_query_fails=query_fails,
    )

    assert result["valid"] is False
    assert expected_failure in result["failures"]


@pytest.mark.parametrize(
    ("field", "value", "expected_failure"),
    (
        ("schemaVersion", "2.0", "schemaVersion"),
        ("status", "fail", "status"),
        ("appName", "another-container-app", "appName"),
        (
            "generatedAt",
            (NOW - MAX_DEPLOYMENT_REPORT_AGE - timedelta(seconds=1)).isoformat(),
            "freshness",
        ),
        ("fqdn", "https://wrong.example.invalid", "fqdn"),
        ("image", "registry.example.invalid/agent-iq-orchestrator:latest", "immutableImage"),
        ("imageDigest", f"sha256:{'b' * 64}", "imageDigest"),
        ("ownershipTags", {"agent-iq-owner": "someone-else"}, "ownershipTags"),
        (
            "ownershipTags",
            {**EXPECTED_DEPLOYMENT_OWNERSHIP_TAGS, "unexpected-tag": "unexpected"},
            "ownershipTags",
        ),
        ("ingress", ["mcp"], "ingress"),
        (
            "containerAppIngress",
            {"external": True, "targetPort": 8080, "transport": "http2"},
            "containerAppIngress",
        ),
        ("authentication", "oauth", "authentication"),
        ("containerAppAuthConfig", "enabled", "containerAppAuthConfig"),
        ("minReplicas", 0, "replicaBounds"),
        ("maxReplicas", 4, "replicaBounds"),
        ("applicationInsights", "", "applicationInsights"),
    ),
)
def test_deployment_evidence_rejects_invalid_report_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
    expected_failure: str,
) -> None:
    path = _write_deployment_report(tmp_path, _deployment_report(**{field: value}))
    result = _deployment_evidence_for_test(path, monkeypatch)

    assert result["valid"] is False
    assert expected_failure in result["failures"]


@pytest.mark.parametrize(
    ("current", "expected_failure"),
    (
        (_current_container_app(revision="agent-iq-orchestrator-canary--old"), "currentRevision"),
        (_current_container_app(fqdn="wrong.example.invalid"), "currentIngressFqdn"),
        (
            _current_container_app(
                image=f"registry.example.invalid/agent-iq-orchestrator@sha256:{'b' * 64}"
            ),
            "currentImageDigest",
        ),
        ({}, "currentState"),
    ),
)
def test_deployment_evidence_rejects_stale_or_mismatched_current_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    current: dict[str, object],
    expected_failure: str,
) -> None:
    path = _write_deployment_report(tmp_path, _deployment_report())
    result = _deployment_evidence_for_test(path, monkeypatch, current=current)

    assert result["valid"] is False
    assert expected_failure in result["failures"]


@pytest.mark.parametrize(
    ("current", "auth_config", "expected_failure"),
    (
        (
            _current_container_app(tags={"agent-iq-owner": "someone-else"}),
            None,
            "currentOwnershipTags",
        ),
        (
            _current_container_app(
                tags={**EXPECTED_DEPLOYMENT_OWNERSHIP_TAGS, "unexpected-tag": "unexpected"}
            ),
            None,
            "currentOwnershipTags",
        ),
        (_current_container_app(external=False), None, "currentIngressExternal"),
        (_current_container_app(target_port=80), None, "currentIngressTargetPort"),
        (_current_container_app(transport="http2"), None, "currentIngressTransport"),
        (_current_container_app(anonymous_canary="false"), None, "currentAuthentication"),
        (
            _current_container_app(public_base_url="https://drift.example.invalid"),
            None,
            "currentPublicBaseUrl",
        ),
        (_current_container_app(public_base_url=""), None, "currentPublicBaseUrl"),
        (
            _current_container_app(),
            {"platform": {"enabled": True}},
            "currentAuthentication",
        ),
        (_current_container_app(min_replicas=0), None, "currentReplicaBounds"),
        (_current_container_app(max_replicas=2), None, "currentReplicaBounds"),
    ),
)
def test_deployment_evidence_rejects_mismatched_live_security_and_scale_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    current: dict[str, object],
    auth_config: dict[str, object] | None,
    expected_failure: str,
) -> None:
    path = _write_deployment_report(tmp_path, _deployment_report())
    result = _deployment_evidence_for_test(
        path,
        monkeypatch,
        current=current,
        auth_config=auth_config,
    )

    assert result["valid"] is False
    assert expected_failure in result["failures"]


def test_public_base_drift_fails_before_transport_probes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write_deployment_report(tmp_path, _deployment_report())
    current = _current_container_app(public_base_url="https://drift.example.invalid")

    monkeypatch.setattr(
        orchestrator_certifier,
        "_query_current_container_app",
        lambda resource_group, app_name: current,
    )
    monkeypatch.setattr(
        orchestrator_certifier,
        "_query_current_container_app_auth",
        lambda resource_group, app_name: {},
    )
    monkeypatch.setattr(
        orchestrator_certifier,
        "_query_current_container_app_revision",
        lambda resource_group, app_name, revision: _current_revision(),
    )

    def forbidden_http_client(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("Transport probes must not run after public-base drift.")

    monkeypatch.setattr(httpx, "AsyncClient", forbidden_http_client)
    result = asyncio.run(
        orchestrator_certifier.certify(
            argparse.Namespace(
                base_url="https://example.invalid",
                resource_group=RESOURCE_GROUP,
                app_name=APP_NAME,
                deployment_report=path,
                app_insights=None,
            )
        )
    )

    assert result["status"] == "fail"
    assert result["deployment"]["checks"]["currentPublicBaseUrl"] is False
    assert result["transports"] == {}


def test_deployment_evidence_rejects_latest_and_non_writer_filenames(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = json.dumps(_deployment_report())
    latest = tmp_path / "latest.json"
    latest.write_text(report, encoding="utf-8")
    wrong_prefix = tmp_path / ("orchestrator-canary-20260711T120000000000Z-" + "a" * 32 + ".json")
    wrong_prefix.write_text(report, encoding="utf-8")

    for path in (latest, wrong_prefix):
        result = _deployment_evidence_for_test(path, monkeypatch)
        assert result["valid"] is False
        assert result["checks"]["reportPath"] is False
        with pytest.raises(argparse.ArgumentTypeError):
            _deployment_report_path_argument(str(path))
