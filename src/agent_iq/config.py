"""Validated, side-effect-free Agent IQ configuration."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import Any, Literal
from unicodedata import category
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from pydantic import AliasChoices, AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_SEARCH_SERVICE_RE = r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$"
_FOUNDRY_PROJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_FOUNDRY_HOST_SUFFIX = ".services.ai.azure.com"
_AZURE_AI_ACCOUNT_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_AZURE_OPENAI_HOST_SUFFIX = ".cognitiveservices.azure.com"
_AZURE_CONTAINER_APPS_HOST_RE = re.compile(
    r"^agent-iq-orchestrator-canary(?:-[a-z0-9]+)?\."
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\."
    r"[a-z0-9]+\."
    r"azurecontainerapps\.io$"
)
ARTIFACT_PUBLISH_PATH = "/v1/artifacts/publish"
ARTIFACT_TICKET_CLAIM_PATH = "/v1/artifacts/tickets/claim"


@dataclass(frozen=True, slots=True)
class ArtifactChannelBinding:
    """Canonical companion endpoint and its immutable Entra resource identity."""

    publish_endpoint: str
    claim_endpoint: str
    application_id: str
    tenant_id: str
    token_audience: str
    token_scope: str


class Environment(StrEnum):
    """Supported deployment safety boundaries."""

    LOCAL = "local"
    CANARY = "canary"
    PRODUCTION = "production"


class SapMode(StrEnum):
    DISABLED = "disabled"
    FIXTURE = "fixture"


class MemoryMode(StrEnum):
    """How Agent IQ treats the optional Memory service."""

    DISABLED = "disabled"
    OPTIONAL = "optional"
    REQUIRED = "required"


class Settings(BaseSettings):
    """Process configuration loaded explicitly by an application entry point."""

    model_config = SettingsConfigDict(
        env_prefix="AGENT_IQ_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    environment: Environment = Field(
        default=Environment.LOCAL,
        validation_alias=AliasChoices("AIQ_ENVIRONMENT", "AGENT_IQ_ENVIRONMENT"),
    )
    native_a2a_required: bool = True
    allow_anonymous_connector: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    foundry_project_endpoint: AnyHttpUrl | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "AGENT_IQ_FOUNDRY_PROJECT_ENDPOINT",
            "FOUNDRY_PROJECT_ENDPOINT",
            "AZURE_AI_PROJECT_ENDPOINT",
        ),
    )
    azure_openai_endpoint: AnyHttpUrl | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "AGENT_IQ_AZURE_OPENAI_ENDPOINT",
            "AZURE_OPENAI_ENDPOINT",
            "AZURE_API_BASE",
        ),
    )
    azure_ai_account: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "AGENT_IQ_AZURE_AI_ACCOUNT",
            "AZURE_AI_ACCOUNT",
        ),
    )
    azure_ai_project: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "AGENT_IQ_AZURE_AI_PROJECT",
            "AZURE_AI_PROJECT",
        ),
    )
    azure_embedding_deployment: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "AGENT_IQ_AZURE_EMBEDDING_DEPLOYMENT",
            "AZURE_EMBEDDING_DEPLOYMENT",
        ),
    )
    azure_embedding_api_version: str = Field(
        default="2024-10-21",
        validation_alias=AliasChoices(
            "AGENT_IQ_AZURE_EMBEDDING_API_VERSION",
            "AZURE_EMBEDDING_API_VERSION",
            "AZURE_API_VERSION",
        ),
    )
    azure_model_deployment: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "AGENT_IQ_AZURE_MODEL_DEPLOYMENT",
            "AZURE_AI_MODEL_DEPLOYMENT_NAME",
            "AZURE_MODEL_DEPLOYMENT",
            "MODEL_DEPLOYMENT_NAME",
        ),
    )
    toolbox_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "AGENT_IQ_TOOLBOX_NAME",
            "TOOLBOX_NAME",
        ),
    )
    toolbox_endpoint: AnyHttpUrl | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "AGENT_IQ_TOOLBOX_ENDPOINT",
            "TOOLBOX_ENDPOINT",
        ),
    )
    search_service: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "AGENT_IQ_SEARCH_SERVICE",
            "AGENT_IQ_CONSUMER_SEARCH_SERVICE",
        ),
    )
    public_base_url: AnyHttpUrl | None = None
    deployment_probe_base_url: AnyHttpUrl | None = None
    artifact_publish_endpoint: AnyHttpUrl | None = None
    artifact_companion_application_id: str | None = None
    artifact_companion_tenant_id: str | None = None
    artifact_token_scope: str | None = None
    sap_mode: SapMode = SapMode.DISABLED
    memory_mode: MemoryMode = MemoryMode.DISABLED
    memory_endpoint: AnyHttpUrl | None = None
    memory_timeout_seconds: float = Field(default=2.0, gt=0, le=30)

    azure_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("AGENT_IQ_AZURE_API_KEY", "AZURE_API_KEY"),
        exclude=True,
        repr=False,
    )
    memory_api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)

    @field_validator("foundry_project_endpoint", mode="before")
    @classmethod
    def canonicalize_foundry_project_endpoint(cls, value: object) -> str | None:
        if value is None:
            return None
        canonical = canonicalize_foundry_project_endpoint(value)
        if value != canonical:
            raise ValueError("Foundry project endpoint must use its exact canonical form.")
        return canonical

    @field_validator("azure_openai_endpoint", mode="before")
    @classmethod
    def validate_azure_openai_endpoint(cls, value: object) -> str | None:
        if value is None:
            return None
        return _canonicalize_azure_openai_origin(value)

    @field_validator("azure_ai_account", mode="before")
    @classmethod
    def validate_azure_ai_account(cls, value: object) -> str | None:
        if value is None:
            return None
        return canonicalize_azure_ai_account(value)

    @field_validator("azure_ai_project", mode="before")
    @classmethod
    def validate_azure_ai_project(cls, value: object) -> str | None:
        if value is None:
            return None
        return canonicalize_azure_ai_project(value)

    @field_validator("toolbox_endpoint", mode="before")
    @classmethod
    def canonicalize_toolbox_endpoint(cls, value: object) -> str | None:
        return _canonicalize_endpoint(
            value,
            field_name="Toolbox endpoint",
            allow_path=True,
            require_https=True,
            allowed_query="api-version=v1",
        )

    @field_validator("artifact_publish_endpoint", mode="before")
    @classmethod
    def canonicalize_artifact_endpoint(cls, value: object) -> str | None:
        if value is None:
            return None
        return canonicalize_artifact_publish_endpoint(value)

    @field_validator("artifact_companion_application_id", mode="before")
    @classmethod
    def canonicalize_artifact_application_id(cls, value: object) -> str | None:
        if value is None:
            return None
        return canonicalize_artifact_companion_application_id(value)

    @field_validator("artifact_companion_tenant_id", mode="before")
    @classmethod
    def canonicalize_artifact_tenant_id(cls, value: object) -> str | None:
        if value is None:
            return None
        return canonicalize_artifact_companion_tenant_id(value)

    @field_validator("public_base_url", mode="before")
    @classmethod
    def canonicalize_public_base_url(cls, value: object) -> str | None:
        return _canonicalize_endpoint(
            value,
            field_name="Public base URL",
            allow_path=True,
            require_https=False,
        )

    @field_validator("memory_endpoint", mode="before")
    @classmethod
    def canonicalize_memory_endpoint(cls, value: object) -> str | None:
        return _canonicalize_endpoint(
            value,
            field_name="Memory endpoint",
            allow_path=True,
            require_https=False,
        )

    @field_validator("deployment_probe_base_url", mode="before")
    @classmethod
    def canonicalize_deployment_probe_base_url(cls, value: object) -> str | None:
        return _canonicalize_endpoint(
            value,
            field_name="Deployment probe base URL",
            allow_path=False,
            require_https=True,
        )

    @field_validator("search_service", mode="before")
    @classmethod
    def canonicalize_search_service(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("Search service must be a string.")
        if (
            not value
            or value != value.strip()
            or any(char.isspace() or category(char) == "Cc" for char in value)
            or "\\" in value
        ):
            raise ValueError("Search service contains unsafe characters.")
        normalized = value.casefold()
        if len(normalized) > 60 or not re.fullmatch(_SEARCH_SERVICE_RE, normalized):
            raise ValueError("Search service must be a DNS-safe Azure resource name.")
        return normalized

    @model_validator(mode="after")
    def validate_safety_boundary(self) -> Settings:
        """Reject unsafe or incomplete non-local configuration."""

        if self.environment is not Environment.LOCAL and self.foundry_project_endpoint is None:
            raise ValueError("A Foundry project endpoint is required outside local mode.")
        if self.foundry_project_endpoint is not None:
            if self.azure_ai_account is None:
                raise ValueError(
                    "An Azure AI account is required when a Foundry project endpoint is configured."
                )
            if self.azure_ai_project is None:
                raise ValueError(
                    "An Azure AI project is required when a Foundry project endpoint is configured."
                )
            canonicalize_foundry_project_endpoint(
                str(self.foundry_project_endpoint),
                expected_account=self.azure_ai_account,
                expected_project=self.azure_ai_project,
            )
        if self.environment is Environment.PRODUCTION and self.allow_anonymous_connector:
            raise ValueError("Anonymous connector access is forbidden in production.")
        if self.environment is not Environment.LOCAL and not self.native_a2a_required:
            raise ValueError("Native A2A cannot be disabled outside local mode.")
        if self.environment is Environment.PRODUCTION and self.sap_mode is SapMode.FIXTURE:
            raise ValueError("Deterministic SAP fixtures are forbidden in production.")
        if self.memory_mode is not MemoryMode.DISABLED and self.memory_endpoint is None:
            raise ValueError("A Memory endpoint is required when Memory is enabled.")
        if self.memory_endpoint is not None:
            memory = urlsplit(str(self.memory_endpoint))
            if self.environment is not Environment.LOCAL and memory.scheme != "https":
                raise ValueError("The Memory endpoint must use HTTPS outside local mode.")
        if self.deployment_probe_base_url is not None:
            if self.public_base_url is None:
                raise ValueError(
                    "A public base URL is required when a deployment probe URL is configured."
                )
            public = urlsplit(str(self.public_base_url))
            probe = urlsplit(str(self.deployment_probe_base_url))
            public_host = public.hostname or ""
            probe_host = probe.hostname or ""
            app_name, separator, environment_domain = public_host.partition(".")
            revision_host = probe_host.removesuffix(f".{environment_domain}")
            revision_pattern = re.compile(
                rf"^{re.escape(app_name)}--[a-z0-9](?:[a-z0-9-]{{0,62}}[a-z0-9])?$"
            )
            if (
                public.scheme != "https"
                or not separator
                or public.path not in {"", "/"}
                or not probe_host.endswith(f".{environment_domain}")
                or probe_host == public_host
                or revision_pattern.fullmatch(revision_host) is None
            ):
                raise ValueError(
                    "Deployment probe base URL must be an HTTPS revision FQDN for "
                    "the configured public Container App."
                )
        artifact_values_present = any(
            value is not None
            for value in (
                self.artifact_publish_endpoint,
                self.artifact_companion_application_id,
                self.artifact_companion_tenant_id,
                self.artifact_token_scope,
            )
        )
        if artifact_values_present and (
            self.artifact_publish_endpoint is None
            or self.artifact_companion_application_id is None
            or self.artifact_companion_tenant_id is None
        ):
            raise ValueError(
                "Artifact publish endpoint, companion application ID, and companion "
                "tenant ID must be configured together."
            )
        if (
            self.artifact_publish_endpoint is not None
            and self.artifact_companion_application_id is not None
            and self.artifact_companion_tenant_id is not None
        ):
            binding = bind_artifact_channel(
                str(self.artifact_publish_endpoint),
                self.artifact_companion_application_id,
                self.artifact_companion_tenant_id,
                token_scope=self.artifact_token_scope,
            )
            self.artifact_token_scope = binding.token_scope
        if self.azure_openai_endpoint is not None:
            if self.azure_ai_account is None:
                raise ValueError(
                    "An Azure AI account is required when an Azure OpenAI endpoint is configured."
                )
            canonicalize_azure_openai_endpoint(
                str(self.azure_openai_endpoint),
                expected_account=self.azure_ai_account,
            )
        return self

    def diagnostic_view(self) -> dict[str, Any]:
        """Return non-secret settings safe for health and support output."""

        return {
            "environment": self.environment.value,
            "native_a2a_required": self.native_a2a_required,
            "allow_anonymous_connector": self.allow_anonymous_connector,
            "log_level": self.log_level,
            "foundry_project_endpoint": _url_text(self.foundry_project_endpoint),
            "azure_openai_endpoint": _url_text(self.azure_openai_endpoint),
            "azure_ai_account": self.azure_ai_account,
            "azure_ai_project": self.azure_ai_project,
            "azure_embedding_deployment": self.azure_embedding_deployment,
            "azure_embedding_api_version": self.azure_embedding_api_version,
            "azure_model_deployment": self.azure_model_deployment,
            "toolbox_name": self.toolbox_name,
            "toolbox_endpoint": _url_text(self.toolbox_endpoint),
            "search_service": self.search_service,
            "public_base_url": _url_text(self.public_base_url),
            "deployment_probe_base_url": _url_text(self.deployment_probe_base_url),
            "artifact_publish_endpoint": _url_text(self.artifact_publish_endpoint),
            "artifact_companion_application_id": self.artifact_companion_application_id,
            "artifact_companion_tenant_id": self.artifact_companion_tenant_id,
            "artifact_token_scope": self.artifact_token_scope,
            "sap_mode": self.sap_mode.value,
            "memory_mode": self.memory_mode.value,
            "memory_endpoint": _url_text(self.memory_endpoint),
            "memory_timeout_seconds": self.memory_timeout_seconds,
            "memory_api_key_configured": self.memory_api_key is not None,
        }

    def artifact_channel(self) -> ArtifactChannelBinding | None:
        """Return the fully bound artifact channel, if configured."""

        if (
            self.artifact_publish_endpoint is None
            or self.artifact_companion_application_id is None
            or self.artifact_companion_tenant_id is None
        ):
            return None
        return bind_artifact_channel(
            str(self.artifact_publish_endpoint),
            self.artifact_companion_application_id,
            self.artifact_companion_tenant_id,
            token_scope=self.artifact_token_scope,
        )

    def search_endpoint(self) -> str:
        """Return the configured keyless Azure AI Search data-plane endpoint."""

        if not self.search_service:
            raise ValueError("AGENT_IQ_SEARCH_SERVICE is required for Foundry IQ evidence.")
        return f"https://{self.search_service}.search.windows.net"


def _url_text(value: AnyHttpUrl | None) -> str | None:
    return str(value) if value is not None else None


def canonicalize_foundry_project_endpoint(
    value: object,
    *,
    expected_account: str | None = None,
    expected_project: str | None = None,
) -> str:
    """Validate and return one canonical Microsoft Foundry project endpoint."""

    field_name = "Foundry project endpoint"
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a URL string.")
    if (
        not value
        or value != value.strip()
        or any(char.isspace() or category(char) == "Cc" for char in value)
        or "\\" in value
        or "%" in value
    ):
        raise ValueError(f"{field_name} contains unsafe characters.")
    if "?" in value or "#" in value:
        raise ValueError(f"{field_name} must not contain a query or fragment.")

    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{field_name} is not a valid URL.") from exc

    if parsed.scheme.casefold() != "https" or not parsed.netloc or not hostname:
        raise ValueError(f"{field_name} must be an absolute HTTPS URL.")
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise ValueError(f"{field_name} must not contain credentials.")
    if port is not None or ":" in parsed.netloc:
        raise ValueError(f"{field_name} must not contain a port.")

    canonical_host = hostname.casefold()
    if not canonical_host.endswith(_FOUNDRY_HOST_SUFFIX):
        raise ValueError(f"{field_name} host must end with {_FOUNDRY_HOST_SUFFIX!r}.")
    account = canonical_host[: -len(_FOUNDRY_HOST_SUFFIX)]
    try:
        canonicalize_azure_ai_account(account)
    except ValueError as exc:
        raise ValueError(f"{field_name} contains an invalid account name.") from exc

    path_match = re.fullmatch(
        r"/api/projects/(?P<project>[A-Za-z0-9][A-Za-z0-9._-]{0,127})/?",
        parsed.path,
    )
    if path_match is None:
        raise ValueError(f"{field_name} must have the path '/api/projects/<project>'.")
    project = path_match.group("project")
    try:
        canonicalize_azure_ai_project(project)
    except ValueError as exc:
        raise ValueError(f"{field_name} contains an invalid project name.") from exc

    canonical = foundry_project_endpoint_for_account(account, project)
    if (expected_account is None) != (expected_project is None):
        raise ValueError("Expected Azure AI account and project must be provided together.")
    if expected_account is not None and expected_project is not None:
        expected = foundry_project_endpoint_for_account(expected_account, expected_project)
        if value != expected or canonical != expected:
            raise ValueError(
                "Foundry project endpoint does not match the configured "
                "Azure AI account and project."
            )
    return canonical


def canonicalize_artifact_publish_endpoint(value: object) -> str:
    """Validate the exact authenticated route on the Agent IQ orchestrator canary."""

    field_name = "Artifact publish endpoint"
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a URL string.")
    if (
        not value
        or value != value.strip()
        or any(char.isspace() or category(char) == "Cc" for char in value)
        or any(delimiter in value for delimiter in ("\\", "%", "@", "?", "#"))
    ):
        raise ValueError(f"{field_name} contains unsafe or noncanonical characters.")

    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{field_name} is not a valid URL.") from exc

    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or not hostname
        or parsed.path != ARTIFACT_PUBLISH_PATH
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{field_name} must be an exact HTTPS {ARTIFACT_PUBLISH_PATH!r} route.")
    if parsed.username is not None or parsed.password is not None or port is not None:
        raise ValueError(f"{field_name} must not contain credentials or a port.")

    canonical_host = hostname.casefold()
    if not _AZURE_CONTAINER_APPS_HOST_RE.fullmatch(canonical_host):
        raise ValueError(
            f"{field_name} must target the Agent IQ orchestrator canary on Azure Container Apps."
        )
    canonical = f"https://{canonical_host}{ARTIFACT_PUBLISH_PATH}"
    if value != canonical:
        raise ValueError(f"{field_name} is not a canonical orchestrator canary route.")
    return canonical


def canonicalize_artifact_companion_application_id(value: object) -> str:
    """Validate a canonical immutable Entra application/client identifier."""

    if not isinstance(value, str):
        raise ValueError("Artifact companion application ID must be a string.")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError("Artifact companion application ID must be a canonical UUID.") from exc
    canonical = str(parsed)
    if parsed.int == 0 or value != canonical:
        raise ValueError("Artifact companion application ID must be a canonical UUID.")
    return canonical


def canonicalize_artifact_companion_tenant_id(value: object) -> str:
    """Validate the immutable Entra tenant that issues companion tokens."""

    if not isinstance(value, str):
        raise ValueError("Artifact companion tenant ID must be a string.")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError("Artifact companion tenant ID must be a canonical UUID.") from exc
    canonical = str(parsed)
    if parsed.int == 0 or value != canonical:
        raise ValueError("Artifact companion tenant ID must be a canonical UUID.")
    return canonical


def bind_artifact_channel(
    publish_endpoint: object,
    companion_application_id: object,
    companion_tenant_id: object,
    *,
    token_scope: object | None = None,
) -> ArtifactChannelBinding:
    """Bind one trusted companion route to the only valid Entra audience and scope."""

    endpoint = canonicalize_artifact_publish_endpoint(publish_endpoint)
    application_id = canonicalize_artifact_companion_application_id(companion_application_id)
    tenant_id = canonicalize_artifact_companion_tenant_id(companion_tenant_id)
    audience = application_id
    expected_scope = f"{application_id}/.default"
    if token_scope is not None and (
        not isinstance(token_scope, str) or token_scope != expected_scope
    ):
        raise ValueError("Artifact token scope does not match the companion application ID.")
    base_url = endpoint.removesuffix(ARTIFACT_PUBLISH_PATH)
    return ArtifactChannelBinding(
        publish_endpoint=endpoint,
        claim_endpoint=f"{base_url}{ARTIFACT_TICKET_CLAIM_PATH}",
        application_id=application_id,
        tenant_id=tenant_id,
        token_audience=audience,
        token_scope=expected_scope,
    )


def canonicalize_azure_ai_account(value: object) -> str:
    """Validate one canonical Azure AI account DNS label."""

    if not isinstance(value, str):
        raise ValueError("Azure AI account must be a string.")
    if (
        not value
        or value != value.strip()
        or any(char.isspace() or category(char) == "Cc" for char in value)
        or not _AZURE_AI_ACCOUNT_RE.fullmatch(value)
    ):
        raise ValueError("Azure AI account must be a canonical lowercase DNS label.")
    return value


def canonicalize_azure_ai_project(value: object) -> str:
    """Validate one canonical Azure AI project resource name."""

    if not isinstance(value, str):
        raise ValueError("Azure AI project must be a string.")
    if (
        not value
        or value != value.strip()
        or any(char.isspace() or category(char) == "Cc" for char in value)
        or not _FOUNDRY_PROJECT_RE.fullmatch(value)
    ):
        raise ValueError("Azure AI project must be a canonical project resource name.")
    return value


def foundry_project_endpoint_for_account(account: object, project: object) -> str:
    """Derive the only accepted Foundry endpoint for an Azure AI account and project."""

    canonical_account = canonicalize_azure_ai_account(account)
    canonical_project = canonicalize_azure_ai_project(project)
    return f"https://{canonical_account}{_FOUNDRY_HOST_SUFFIX}/api/projects/{canonical_project}"


def azure_openai_endpoint_for_account(account: object) -> str:
    """Derive the only accepted Azure OpenAI origin for an Azure AI account."""

    canonical_account = canonicalize_azure_ai_account(account)
    return f"https://{canonical_account}{_AZURE_OPENAI_HOST_SUFFIX}/"


def canonicalize_azure_openai_endpoint(
    value: object,
    *,
    expected_account: str,
) -> str:
    """Validate a canonical Azure Cognitive Services origin bound to one Azure AI account."""

    canonical = _canonicalize_azure_openai_origin(value)
    expected = azure_openai_endpoint_for_account(expected_account)
    if canonical != expected:
        raise ValueError("Azure OpenAI endpoint does not match the configured Azure AI account.")
    return canonical


def _canonicalize_azure_openai_origin(value: object) -> str:
    """Validate the shape of one canonical Azure Cognitive Services origin."""

    field_name = "Azure OpenAI endpoint"
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a URL string.")
    if (
        not value
        or value != value.strip()
        or any(char.isspace() or category(char) == "Cc" for char in value)
        or any(delimiter in value for delimiter in ("\\", "%", "@", "?", "#"))
    ):
        raise ValueError(f"{field_name} contains unsafe or noncanonical characters.")

    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{field_name} is not a valid URL.") from exc

    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or not hostname
        or parsed.path != "/"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{field_name} must be a canonical HTTPS origin.")
    if parsed.username is not None or parsed.password is not None or port is not None:
        raise ValueError(f"{field_name} must not contain credentials or a port.")

    canonical_host = hostname.casefold()
    if not canonical_host.endswith(_AZURE_OPENAI_HOST_SUFFIX):
        raise ValueError(f"{field_name} host must end with {_AZURE_OPENAI_HOST_SUFFIX!r}.")
    account = canonical_host[: -len(_AZURE_OPENAI_HOST_SUFFIX)]
    canonicalize_azure_ai_account(account)
    canonical = azure_openai_endpoint_for_account(account)
    if value != canonical:
        raise ValueError(f"{field_name} must use its exact canonical Azure origin.")
    return canonical


def _canonicalize_endpoint(
    value: object,
    *,
    field_name: str,
    allow_path: bool,
    require_https: bool,
    allowed_query: str | None = None,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a URL string.")
    if (
        not value
        or value != value.strip()
        or any(char.isspace() or category(char) == "Cc" for char in value)
        or "\\" in value
    ):
        raise ValueError(f"{field_name} contains unsafe characters.")
    if "#" in value:
        raise ValueError(f"{field_name} must not contain a query or fragment.")

    try:
        parsed = urlsplit(value)
        scheme = parsed.scheme.casefold()
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{field_name} is not a valid URL.") from exc

    allowed_schemes = {"https"} if require_https else {"http", "https"}
    if scheme not in allowed_schemes or not parsed.netloc or not hostname:
        scheme_text = "HTTPS" if require_https else "HTTP or HTTPS"
        raise ValueError(f"{field_name} must be an absolute {scheme_text} URL.")
    if ("?" in value or parsed.query) and (allowed_query is None or parsed.query != allowed_query):
        raise ValueError(f"{field_name} must not contain a query or fragment.")
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise ValueError(f"{field_name} must not contain credentials.")
    if "%" in parsed.netloc or parsed.netloc.endswith(":"):
        raise ValueError(f"{field_name} contains a noncanonical host.")

    try:
        canonical_host = hostname.encode("idna").decode("ascii").casefold()
    except UnicodeError as exc:
        raise ValueError(f"{field_name} contains an invalid host.") from exc
    if not canonical_host:
        raise ValueError(f"{field_name} must contain a host.")
    if ":" in canonical_host:
        canonical_host = f"[{canonical_host}]"
    canonical_netloc = canonical_host
    if port is not None and not (
        (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    ):
        canonical_netloc += f":{port}"

    path = _canonicalize_path(parsed.path, field_name=field_name, allow_path=allow_path)
    return urlunsplit((scheme, canonical_netloc, path, parsed.query, ""))


def _canonicalize_path(path: str, *, field_name: str, allow_path: bool) -> str:
    path = path or "/"
    if not path.startswith("/") or "%" in path or "//" in path:
        raise ValueError(f"{field_name} contains an unsafe or noncanonical path.")
    if any(segment in {".", ".."} for segment in path.split("/")):
        raise ValueError(f"{field_name} contains an unsafe or noncanonical path.")
    if not allow_path and path != "/":
        raise ValueError(f"{field_name} must be an origin without a path.")
    if path != "/":
        path = path.rstrip("/")
    return path or "/"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and cache settings when the application explicitly requests them."""

    return Settings()
