"""Keyless access to a Foundry toolbox over its managed MCP endpoint."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Generator, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

import httpx
from azure.core.credentials import TokenCredential
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent_iq.config import Settings, get_settings
from agent_iq.connectors.entra import isolated_default_azure_credential
from agent_iq.tls import hosted_server_tls_context

FOUNDRY_SCOPE = "https://ai.azure.com/.default"
_SOURCE_REFERENCE = re.compile(r"\[ref_id:\d+\]", re.IGNORECASE)


class ToolboxClient(Protocol):
    """Minimal MCP client surface used by the hosted runtime."""

    async def get_tools(self, *, server_name: str | None = None) -> list[BaseTool]: ...


@dataclass(frozen=True, slots=True)
class SourceBoundPayload:
    """Canonical machine payload released only after its source is retrieved."""

    source_title: str
    start_marker: str
    end_marker: str
    payload: str

    def __post_init__(self) -> None:
        values = (self.source_title, self.start_marker, self.end_marker, self.payload)
        if any(not value.strip() or value != value.strip() for value in values):
            raise ValueError("Source-bound payload fields must be non-empty and trimmed.")
        if (
            self.payload.count(self.start_marker) != 1
            or self.payload.count(self.end_marker) != 1
            or not self.payload.startswith(self.start_marker)
            or not self.payload.endswith(self.end_marker)
        ):
            raise ValueError("Source-bound payload markers must delimit the complete payload.")


class _KnowledgeQueries(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    queries: list[str] = Field(
        min_length=1,
        max_length=1,
        description="One focused question to retrieve from the approved knowledge base.",
    )

    @field_validator("queries")
    @classmethod
    def validate_queries(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("Knowledge queries cannot be empty.")
        return values


class _LazyKnowledgeBackend:
    """Resolve one approved managed-MCP tool only when the model invokes it."""

    def __init__(
        self,
        loader: Callable[[], Awaitable[Sequence[BaseTool]]],
        source_bound_payload: SourceBoundPayload | None,
    ) -> None:
        self._loader = loader
        self._source_bound_payload = source_bound_payload
        self._tool: BaseTool | None = None
        self._lock = asyncio.Lock()

    async def invoke(self, queries: list[str]) -> Any:
        tool = await self._resolve()
        query = queries[0]
        if "queries" in tool.args:
            arguments: dict[str, object] = {"queries": [query]}
        elif "query" in tool.args:
            arguments = {"query": query}
        else:
            raise RuntimeError(
                "The approved Foundry IQ tool does not advertise a supported query schema."
            )
        result = await tool.ainvoke(arguments)
        text = _knowledge_result_text(result)
        if self._source_bound_payload is None:
            return text
        return _bind_source_payload(text, self._source_bound_payload)

    async def _resolve(self) -> BaseTool:
        async with self._lock:
            if self._tool is None:
                tools = list(await self._loader())
                compatible = [
                    tool for tool in tools if "queries" in tool.args or "query" in tool.args
                ]
                if len(tools) != 1 or len(compatible) != 1:
                    raise RuntimeError(
                        "The configured Foundry toolbox must expose exactly one compatible "
                        "knowledge-base retrieval tool."
                    )
                self._tool = compatible[0]
            return self._tool


class ToolboxAuth(httpx.Auth):
    """Mint a fresh Entra token for every long-lived toolbox MCP request."""

    def __init__(self, token_provider: Callable[[], str]) -> None:
        self._token_provider = token_provider

    def auth_flow(
        self,
        request: httpx.Request,
    ) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers["Authorization"] = f"Bearer {self._token_provider()}"
        yield request


def _isolated_httpx_client(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    """Create an MCP client that cannot proxy or redirect bearer credentials."""

    return httpx.AsyncClient(
        headers=headers,
        timeout=timeout,
        auth=auth,
        verify=hosted_server_tls_context(),
        follow_redirects=False,
        trust_env=False,
    )


def toolbox_mcp_url(settings: Settings) -> str:
    """Resolve an explicit toolbox endpoint or the current project-scoped route."""

    if settings.toolbox_endpoint is not None:
        return str(settings.toolbox_endpoint)
    if settings.foundry_project_endpoint is None:
        raise ValueError("FOUNDRY_PROJECT_ENDPOINT is required to resolve the toolbox route.")
    if not settings.toolbox_name:
        raise ValueError("TOOLBOX_NAME is required to resolve the toolbox route.")
    project_endpoint = str(settings.foundry_project_endpoint).rstrip("/")
    return f"{project_endpoint}/toolboxes/{settings.toolbox_name}/mcp?api-version=v1"


def build_toolbox_client(
    settings: Settings,
    credential: TokenCredential,
) -> ToolboxClient:
    """Create the current LangChain MCP adapter with refreshable Entra auth."""

    from langchain_mcp_adapters.client import MultiServerMCPClient
    from langchain_mcp_adapters.sessions import StreamableHttpConnection

    token_provider = get_bearer_token_provider(credential, FOUNDRY_SCOPE)
    connection: StreamableHttpConnection = {
        "transport": "streamable_http",
        "url": toolbox_mcp_url(settings),
        "auth": ToolboxAuth(token_provider),
        "httpx_client_factory": _isolated_httpx_client,
    }
    return cast(
        ToolboxClient,
        MultiServerMCPClient(
            {"knowledge-base": connection},
            handle_tool_errors=False,
        ),
    )


def build_lazy_knowledge_tool(
    settings: Settings | None = None,
    *,
    credential: TokenCredential | None = None,
    client: ToolboxClient | None = None,
    loader: Callable[[], Awaitable[Sequence[BaseTool]]] | None = None,
    source_bound_payload: SourceBoundPayload | None = None,
) -> BaseTool:
    """Expose a stable local schema while deferring MCP discovery until invocation."""

    active_settings = settings or get_settings()

    async def load() -> Sequence[BaseTool]:
        if loader is not None:
            return await loader()
        return await load_toolbox_tools(
            active_settings,
            credential=credential,
            client=client,
        )

    backend = _LazyKnowledgeBackend(load, source_bound_payload)
    return StructuredTool.from_function(
        coroutine=backend.invoke,
        name="knowledge_base_retrieve",
        description=(
            "Retrieve grounded evidence from this agent's approved Foundry IQ "
            "knowledge base. Submit one focused query."
        ),
        args_schema=_KnowledgeQueries,
    )


async def load_toolbox_tools(
    settings: Settings | None = None,
    *,
    credential: TokenCredential | None = None,
    client: ToolboxClient | None = None,
) -> list[BaseTool]:
    """Load the tools exposed by the configured Foundry toolbox."""

    if client is None:
        active_settings = settings or get_settings()
        active_credential = credential or isolated_default_azure_credential(
            credential_factory=DefaultAzureCredential,
        )
        client = build_toolbox_client(active_settings, active_credential)
    tools = await client.get_tools()
    if not tools:
        raise RuntimeError("The configured Foundry toolbox exposed no tools.")
    return tools


def _knowledge_result_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    blocks: Sequence[Any]
    if isinstance(result, dict):
        blocks = (result,)
    elif isinstance(result, Sequence) and not isinstance(result, (bytes, bytearray)):
        blocks = result
    else:
        return str(result)
    texts = [
        str(block["text"]).strip()
        for block in blocks
        if isinstance(block, dict)
        and block.get("type") == "text"
        and str(block.get("text") or "").strip()
    ]
    return "\n".join(texts) if texts else str(result)


def _bind_source_payload(text: str, payload: SourceBoundPayload) -> str:
    if not _has_bound_source_reference(text, payload.source_title):
        raise RuntimeError(
            "Foundry IQ did not return the required source title and reference marker; "
            "the canonical machine payload was not released."
        )
    block = re.compile(
        rf"{re.escape(payload.start_marker)}.*?{re.escape(payload.end_marker)}",
        re.DOTALL,
    )
    grounded_text = block.sub("", text)
    grounded_text = grounded_text.replace(payload.start_marker, "").replace(
        payload.end_marker,
        "",
    )
    grounded_text = grounded_text.strip()
    return f"{grounded_text}\n\n{payload.payload}" if grounded_text else payload.payload


def _has_bound_source_reference(text: str, source_title: str) -> bool:
    title = re.compile(
        rf"(?<!\w){re.escape(source_title)}(?!\w)",
        re.IGNORECASE,
    )
    return any(title.search(line) and _SOURCE_REFERENCE.search(line) for line in text.splitlines())
