"""Configuration safety tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_iq.config import (
    Environment,
    MemoryMode,
    SapMode,
    Settings,
    azure_openai_endpoint_for_account,
    canonicalize_azure_ai_project,
    canonicalize_azure_openai_endpoint,
    canonicalize_foundry_project_endpoint,
    foundry_project_endpoint_for_account,
    get_settings,
)

AZURE_AI_ACCOUNT = "agent-iq-ai"
AZURE_AI_PROJECT = "canary"
FOUNDRY_ENDPOINT = (
    f"https://{AZURE_AI_ACCOUNT}.services.ai.azure.com/api/projects/{AZURE_AI_PROJECT}"
)
AZURE_OPENAI_ENDPOINT = "https://agent-iq-ai.cognitiveservices.azure.com/"
ARTIFACT_APPLICATION_ID = "11111111-1111-4111-8111-abcdefabcdef"
ARTIFACT_TENANT_ID = "22222222-2222-4222-8222-abcdefabcdef"
ORCHESTRATOR_BASE_URL = (
    "https://agent-iq-orchestrator-canary.bluefield-12345678.swedencentral.azurecontainerapps.io"
)
ARTIFACT_ENDPOINT = f"{ORCHESTRATOR_BASE_URL}/v1/artifacts/publish"
ARTIFACT_SCOPE = f"{ARTIFACT_APPLICATION_ID}/.default"


def test_local_defaults_are_safe() -> None:
    settings = Settings(_env_file=None)

    assert settings.environment is Environment.LOCAL
    assert settings.native_a2a_required is True
    assert settings.allow_anonymous_connector is False
    assert settings.foundry_project_endpoint is None
    assert settings.azure_ai_project is None
    assert settings.sap_mode is SapMode.DISABLED
    assert settings.memory_mode is MemoryMode.DISABLED
    assert settings.memory_endpoint is None


def test_canary_requires_foundry_endpoint() -> None:
    with pytest.raises(ValidationError, match="Foundry project endpoint"):
        Settings(environment=Environment.CANARY, _env_file=None)


def test_production_rejects_anonymous_connector() -> None:
    with pytest.raises(ValidationError, match="Anonymous connector"):
        Settings(
            environment=Environment.PRODUCTION,
            foundry_project_endpoint=FOUNDRY_ENDPOINT,
            azure_ai_account=AZURE_AI_ACCOUNT,
            azure_ai_project=AZURE_AI_PROJECT,
            allow_anonymous_connector=True,
            _env_file=None,
        )


def test_non_local_mode_requires_native_a2a() -> None:
    with pytest.raises(ValidationError, match="Native A2A"):
        Settings(
            environment=Environment.CANARY,
            foundry_project_endpoint=FOUNDRY_ENDPOINT,
            azure_ai_account=AZURE_AI_ACCOUNT,
            azure_ai_project=AZURE_AI_PROJECT,
            native_a2a_required=False,
            _env_file=None,
        )


def test_production_rejects_deterministic_sap_fixture_mode() -> None:
    with pytest.raises(ValidationError, match="SAP fixtures"):
        Settings(
            environment=Environment.PRODUCTION,
            foundry_project_endpoint=FOUNDRY_ENDPOINT,
            azure_ai_account=AZURE_AI_ACCOUNT,
            azure_ai_project=AZURE_AI_PROJECT,
            sap_mode=SapMode.FIXTURE,
            _env_file=None,
        )


def test_secret_is_excluded_from_diagnostics_and_repr() -> None:
    secret = "test-only-placeholder-value"  # pragma: allowlist secret
    settings = Settings(azure_api_key=secret, _env_file=None)

    assert settings.azure_api_key is not None
    assert settings.azure_api_key.get_secret_value() == secret
    assert secret not in repr(settings)
    assert "azure_api_key" not in settings.diagnostic_view()
    assert "azure_api_key" not in settings.model_dump()


def test_memory_config_is_disabled_by_default_and_secret_safe() -> None:
    secret = "memory-test-placeholder"  # pragma: allowlist secret
    settings = Settings(
        memory_endpoint="http://127.0.0.1:8077/api/v1/",
        memory_api_key=secret,
        _env_file=None,
    )

    assert settings.memory_mode is MemoryMode.DISABLED
    assert str(settings.memory_endpoint) == "http://127.0.0.1:8077/api/v1"
    assert settings.memory_api_key is not None
    assert settings.memory_api_key.get_secret_value() == secret
    assert secret not in repr(settings)
    assert "memory_api_key" not in settings.diagnostic_view()
    assert "memory_api_key" not in settings.model_dump()
    assert settings.diagnostic_view()["memory_api_key_configured"] is True


def test_enabled_memory_requires_endpoint() -> None:
    with pytest.raises(ValidationError, match="Memory endpoint"):
        Settings(memory_mode=MemoryMode.OPTIONAL, _env_file=None)


def test_non_local_memory_requires_https() -> None:
    with pytest.raises(ValidationError, match="Memory endpoint must use HTTPS"):
        Settings(
            environment=Environment.CANARY,
            foundry_project_endpoint=FOUNDRY_ENDPOINT,
            azure_ai_account=AZURE_AI_ACCOUNT,
            azure_ai_project=AZURE_AI_PROJECT,
            memory_mode=MemoryMode.REQUIRED,
            memory_endpoint="http://memory.example.invalid/api/v1",
            _env_file=None,
        )


def test_artifact_channel_is_bound_to_companion_application_id() -> None:
    with pytest.raises(ValidationError, match="companion application ID"):
        Settings(
            artifact_publish_endpoint=ARTIFACT_ENDPOINT,
            _env_file=None,
        )
    settings = Settings(
        artifact_publish_endpoint=ARTIFACT_ENDPOINT,
        artifact_companion_application_id=ARTIFACT_APPLICATION_ID,
        artifact_companion_tenant_id=ARTIFACT_TENANT_ID,
        _env_file=None,
    )
    assert settings.diagnostic_view()["artifact_token_scope"] == ARTIFACT_SCOPE
    channel = settings.artifact_channel()
    assert channel is not None
    assert channel.claim_endpoint.endswith("/v1/artifacts/tickets/claim")
    assert channel.token_audience == ARTIFACT_APPLICATION_ID
    assert channel.tenant_id == ARTIFACT_TENANT_ID


@pytest.mark.parametrize(
    "endpoint",
    [
        (
            "https://attacker.bluefield-12345678.swedencentral."
            "azurecontainerapps.io/v1/artifacts/publish"
        ),
        ("https://agent-iq-orchestrator-canary.attacker.example/v1/artifacts/publish"),
        (
            "https://agent-iq-orchestrator-canary.bluefield-12345678."
            "swedencentral.azurecontainerapps.io/v1/artifacts/wrong"
        ),
        (
            "https://user@agent-iq-orchestrator-canary.bluefield-12345678."
            "swedencentral.azurecontainerapps.io/v1/artifacts/publish"
        ),
        (
            "https://agent-iq-orchestrator-canary.bluefield-12345678."
            "swedencentral.azurecontainerapps.io:443/v1/artifacts/publish"
        ),
        f"{ARTIFACT_ENDPOINT}?next=https://attacker.example",
        f"{ARTIFACT_ENDPOINT}#fragment",
        ARTIFACT_ENDPOINT.replace("/v1/", "/%2e%2e/v1/"),
        ARTIFACT_ENDPOINT.replace("/v1/", "\\v1\\"),
    ],
)
def test_artifact_channel_rejects_untrusted_endpoint_shapes(endpoint: str) -> None:
    with pytest.raises(ValidationError):
        Settings(
            artifact_publish_endpoint=endpoint,
            artifact_companion_application_id=ARTIFACT_APPLICATION_ID,
            artifact_companion_tenant_id=ARTIFACT_TENANT_ID,
            _env_file=None,
        )


@pytest.mark.parametrize(
    ("application_id", "scope"),
    [
        ("not-a-uuid", None),
        ("00000000-0000-0000-0000-000000000000", None),
        (ARTIFACT_APPLICATION_ID.upper(), None),
        (ARTIFACT_APPLICATION_ID, "https://management.azure.com/.default"),
        (ARTIFACT_APPLICATION_ID, "22222222-2222-4222-8222-222222222222/.default"),
        (ARTIFACT_APPLICATION_ID, ARTIFACT_APPLICATION_ID),
    ],
)
def test_artifact_channel_rejects_malformed_or_mismatched_audience(
    application_id: str,
    scope: str | None,
) -> None:
    with pytest.raises(ValidationError):
        Settings(
            artifact_publish_endpoint=ARTIFACT_ENDPOINT,
            artifact_companion_application_id=application_id,
            artifact_companion_tenant_id=ARTIFACT_TENANT_ID,
            artifact_token_scope=scope,
            _env_file=None,
        )


def test_get_settings_loads_explicit_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_IQ_ENVIRONMENT", "canary")
    monkeypatch.setenv(
        "AGENT_IQ_FOUNDRY_PROJECT_ENDPOINT",
        FOUNDRY_ENDPOINT,
    )
    monkeypatch.setenv("AGENT_IQ_AZURE_AI_ACCOUNT", AZURE_AI_ACCOUNT)
    monkeypatch.setenv("AGENT_IQ_AZURE_AI_PROJECT", AZURE_AI_PROJECT)
    get_settings.cache_clear()

    try:
        settings = get_settings()
        assert settings.environment is Environment.CANARY
        assert str(settings.foundry_project_endpoint) == FOUNDRY_ENDPOINT
    finally:
        get_settings.cache_clear()


def test_azure_compatibility_aliases_are_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIQ_ENVIRONMENT", "canary")
    monkeypatch.setenv(
        "AZURE_AI_PROJECT_ENDPOINT",
        FOUNDRY_ENDPOINT,
    )
    monkeypatch.setenv("AGENT_IQ_AZURE_AI_ACCOUNT", AZURE_AI_ACCOUNT)
    monkeypatch.setenv("AGENT_IQ_AZURE_AI_PROJECT", AZURE_AI_PROJECT)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", AZURE_OPENAI_ENDPOINT)
    monkeypatch.setenv("AZURE_EMBEDDING_DEPLOYMENT", "embedding-model")
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "chat-model")
    monkeypatch.setenv("TOOLBOX_NAME", "knowledge-tools")
    monkeypatch.setenv("TOOLBOX_ENDPOINT", "https://example.invalid/toolboxes/knowledge/mcp")
    settings = Settings(_env_file=None)

    assert settings.environment is Environment.CANARY
    assert str(settings.foundry_project_endpoint) == FOUNDRY_ENDPOINT
    assert settings.azure_ai_account == AZURE_AI_ACCOUNT
    assert settings.azure_ai_project == AZURE_AI_PROJECT
    assert str(settings.azure_openai_endpoint) == AZURE_OPENAI_ENDPOINT
    assert settings.azure_embedding_deployment == "embedding-model"
    assert settings.azure_model_deployment == "chat-model"
    assert settings.toolbox_name == "knowledge-tools"
    assert str(settings.toolbox_endpoint) == "https://example.invalid/toolboxes/knowledge/mcp"
    assert settings.diagnostic_view()["toolbox_name"] == "knowledge-tools"


def test_search_endpoint_requires_and_normalizes_service_name() -> None:
    settings = Settings(search_service="agent-iq-search", _env_file=None)
    assert settings.search_endpoint() == "https://agent-iq-search.search.windows.net"

    with pytest.raises(ValueError, match="SEARCH_SERVICE"):
        Settings(_env_file=None).search_endpoint()


def test_endpoint_urls_are_canonicalized_before_use() -> None:
    settings = Settings(
        foundry_project_endpoint=FOUNDRY_ENDPOINT,
        azure_ai_account=AZURE_AI_ACCOUNT,
        azure_ai_project=AZURE_AI_PROJECT,
        azure_openai_endpoint=AZURE_OPENAI_ENDPOINT,
        toolbox_endpoint="HTTPS://EXAMPLE.INVALID/toolbox/mcp/",
        public_base_url="HTTP://EXAMPLE.INVALID/base/",
        artifact_publish_endpoint=ARTIFACT_ENDPOINT,
        artifact_companion_application_id=ARTIFACT_APPLICATION_ID,
        artifact_companion_tenant_id=ARTIFACT_TENANT_ID,
        search_service="Agent-IQ-Search",
        _env_file=None,
    )

    assert str(settings.foundry_project_endpoint) == FOUNDRY_ENDPOINT
    assert str(settings.azure_openai_endpoint) == AZURE_OPENAI_ENDPOINT
    assert str(settings.toolbox_endpoint) == "https://example.invalid/toolbox/mcp"
    assert str(settings.public_base_url) == "http://example.invalid/base"
    assert str(settings.artifact_publish_endpoint) == ARTIFACT_ENDPOINT
    assert settings.search_endpoint() == "https://agent-iq-search.search.windows.net"


def test_deployment_probe_url_is_bound_to_public_container_app_revision() -> None:
    public_base_url = (
        "https://agent-iq-orchestrator-canary.bluefield-12345678."
        "swedencentral.azurecontainerapps.io"
    )
    probe_base_url = (
        "https://agent-iq-orchestrator-canary--candidate-a1b2c3d4."
        "bluefield-12345678.swedencentral.azurecontainerapps.io"
    )
    settings = Settings(
        public_base_url=public_base_url,
        deployment_probe_base_url=probe_base_url,
        _env_file=None,
    )

    assert str(settings.deployment_probe_base_url).rstrip("/") == probe_base_url
    assert settings.diagnostic_view()["deployment_probe_base_url"] == f"{probe_base_url}/"


@pytest.mark.parametrize(
    ("public_base_url", "probe_base_url"),
    [
        (None, "https://agent-iq-orchestrator-canary--candidate-a.example.test"),
        ("https://agent-iq.example.test", "https://other--candidate-a.example.test"),
        (
            "https://agent-iq.example.test",
            "https://agent-iq--candidate-a.other.example.test",
        ),
        ("http://agent-iq.example.test", "https://agent-iq--candidate-a.example.test"),
        (
            "https://agent-iq.example.test/base",
            "https://agent-iq--candidate-a.example.test",
        ),
        (
            "https://agent-iq.example.test",
            "https://agent-iq--candidate.a.example.test",
        ),
    ],
)
def test_deployment_probe_url_rejects_unbound_origin(
    public_base_url: str | None,
    probe_base_url: str,
) -> None:
    with pytest.raises(ValidationError, match=r"deployment probe URL|Deployment probe"):
        Settings(
            public_base_url=public_base_url,
            deployment_probe_base_url=probe_base_url,
            _env_file=None,
        )


@pytest.mark.parametrize(
    "endpoint",
    [
        FOUNDRY_ENDPOINT.replace(
            "https://agent-iq-ai",
            "HTTPS://AGENT-IQ-AI",
        ),
        f"{FOUNDRY_ENDPOINT}/",
    ],
)
def test_settings_reject_noncanonical_foundry_endpoint_spellings(endpoint: str) -> None:
    with pytest.raises(ValidationError, match="exact canonical form"):
        Settings(
            foundry_project_endpoint=endpoint,
            azure_ai_account=AZURE_AI_ACCOUNT,
            azure_ai_project=AZURE_AI_PROJECT,
            _env_file=None,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (
            "foundry_project_endpoint",
            "https://user:secret@example.invalid/projects/demo",  # pragma: allowlist secret
        ),
        ("azure_openai_endpoint", f"{AZURE_OPENAI_ENDPOINT}?api-key=secret"),
        ("toolbox_endpoint", "https://example.invalid/toolbox#mcp"),
        ("public_base_url", "https://example.invalid/base\\mcp"),
        ("artifact_publish_endpoint", "https://example.invalid/artifacts/%2e%2e/publish"),
        ("foundry_project_endpoint", "https://example.invalid/projects/../demo"),
        ("toolbox_endpoint", "https://example.invalid//toolbox/mcp"),
        ("azure_openai_endpoint", f"{AZURE_OPENAI_ENDPOINT}openai/v1"),
        ("public_base_url", "https://example.invalid/base\nunsafe"),
        ("artifact_publish_endpoint", "https://example.invalid/artifacts?token=secret"),
    ],
)
def test_endpoint_urls_reject_unsafe_or_noncanonical_values(field: str, value: str) -> None:
    values: dict[str, object] = {field: value}
    if field == "artifact_publish_endpoint":
        values["artifact_companion_application_id"] = ARTIFACT_APPLICATION_ID
        values["artifact_companion_tenant_id"] = ARTIFACT_TENANT_ID

    with pytest.raises(ValidationError):
        Settings.model_validate(values)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example.invalid/",
        "https://attacker.cognitiveservices.azure.com/",
        "https://agent-iq-ai.cognitiveservices.azure.com.attacker.example/",
        "https://user@agent-iq-ai.cognitiveservices.azure.com/",
        "https://agent-iq-ai.cognitiveservices.azure.com:443/",
        "https://agent-iq-ai%2ecognitiveservices.azure.com/",
        "https://agent-iq-ai.cognitiveservices.azure.com/?api-version=v1",
        "https://agent-iq-ai.cognitiveservices.azure.com/#fragment",
        "https://agent-iq-ai.cognitiveservices.azure.com/openai",
        "HTTPS://AGENT-IQ-AI.COGNITIVESERVICES.AZURE.COM/",
        "https://agent-iq-ai.cognitiveservices.azure.com",
        "https://agent-iq-ai.cognitiveservices.azure.com/\nunsafe",
    ],
)
def test_azure_openai_endpoint_rejects_noncanonical_or_unbound_origins(endpoint: str) -> None:
    with pytest.raises(ValueError):
        canonicalize_azure_openai_endpoint(
            endpoint,
            expected_account=AZURE_AI_ACCOUNT,
        )
    with pytest.raises(ValidationError):
        Settings(
            azure_ai_account=AZURE_AI_ACCOUNT,
            azure_openai_endpoint=endpoint,
            _env_file=None,
        )


def test_azure_openai_endpoint_is_derived_and_bound_to_hyphenated_account() -> None:
    assert azure_openai_endpoint_for_account(AZURE_AI_ACCOUNT) == AZURE_OPENAI_ENDPOINT
    settings = Settings(
        azure_ai_account=AZURE_AI_ACCOUNT,
        azure_openai_endpoint=AZURE_OPENAI_ENDPOINT,
        _env_file=None,
    )

    assert settings.azure_ai_account == AZURE_AI_ACCOUNT
    assert str(settings.azure_openai_endpoint) == AZURE_OPENAI_ENDPOINT


def test_azure_openai_endpoint_requires_its_matching_account() -> None:
    with pytest.raises(ValidationError, match="Azure AI account is required"):
        Settings(
            azure_openai_endpoint=AZURE_OPENAI_ENDPOINT,
            _env_file=None,
        )
    with pytest.raises(ValidationError, match="does not match the configured Azure AI account"):
        Settings(
            azure_ai_account="different-account",
            azure_openai_endpoint=AZURE_OPENAI_ENDPOINT,
            _env_file=None,
        )

    offline_settings = Settings(azure_ai_account=AZURE_AI_ACCOUNT, _env_file=None)
    assert offline_settings.azure_openai_endpoint is None


def test_foundry_project_endpoint_is_derived_from_explicit_identity() -> None:
    assert canonicalize_azure_ai_project(AZURE_AI_PROJECT) == AZURE_AI_PROJECT
    assert (
        foundry_project_endpoint_for_account(AZURE_AI_ACCOUNT, AZURE_AI_PROJECT) == FOUNDRY_ENDPOINT
    )
    assert (
        canonicalize_foundry_project_endpoint(
            FOUNDRY_ENDPOINT,
            expected_account=AZURE_AI_ACCOUNT,
            expected_project=AZURE_AI_PROJECT,
        )
        == FOUNDRY_ENDPOINT
    )

    settings = Settings(
        foundry_project_endpoint=FOUNDRY_ENDPOINT,
        azure_ai_account=AZURE_AI_ACCOUNT,
        azure_ai_project=AZURE_AI_PROJECT,
        _env_file=None,
    )
    assert str(settings.foundry_project_endpoint) == FOUNDRY_ENDPOINT


@pytest.mark.parametrize(
    ("account", "project", "error"),
    [
        (None, None, "Azure AI account is required"),
        (AZURE_AI_ACCOUNT, None, "Azure AI project is required"),
        ("other-account", AZURE_AI_PROJECT, "does not match"),
        (AZURE_AI_ACCOUNT, "other-project", "does not match"),
    ],
)
def test_foundry_project_endpoint_requires_matching_explicit_identity(
    account: str | None,
    project: str | None,
    error: str,
) -> None:
    with pytest.raises(ValidationError, match=error):
        Settings(
            foundry_project_endpoint=FOUNDRY_ENDPOINT,
            azure_ai_account=account,
            azure_ai_project=project,
            _env_file=None,
        )


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://attacker.example/api/projects/demo",
        "https://example.services.ai.azure.com.attacker.example/api/projects/demo",
        "https://example.services.ai.azure.com@attacker.example/api/projects/demo",
        "https://user@example.services.ai.azure.com/api/projects/demo",
        "https://example.services.ai.azure.com:443/api/projects/demo",
        "https://example%2eservices.ai.azure.com/api/projects/demo",
        "https://example.services.ai.azure.com/api/projects/%2e%2e",
        "https://example.services.ai.azure.com/api/projects/demo/extra",
        "https://example.services.ai.azure.com//api/projects/demo",
        "https://example.services.ai.azure.com/api/projects/demo?api-version=v1",
        "https://example.services.ai.azure.com/api/projects/demo#fragment",
        "https://example.services.ai.azure.com/api/projects/demo\\@attacker.example",
        "https://example.services.ai.azure.com/api/projects/demo\nunsafe",
    ],
)
def test_foundry_project_endpoint_rejects_authority_and_path_tricks(endpoint: str) -> None:
    with pytest.raises(ValueError):
        canonicalize_foundry_project_endpoint(endpoint)


@pytest.mark.parametrize(
    "search_service",
    [
        "search/name",
        "search\\name",
        "search?secret",
        "search#fragment",
        "search name",
        "search\nname",
        "search.example",
    ],
)
def test_search_service_rejects_host_injection_values(search_service: str) -> None:
    with pytest.raises(ValidationError):
        Settings(search_service=search_service, _env_file=None)
