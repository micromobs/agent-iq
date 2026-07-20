"""Managed Foundry toolbox connector tests."""

from __future__ import annotations

import asyncio
import ssl
from collections.abc import Sequence
from typing import Any, cast

import httpx
import pytest
from azure.core.credentials import AccessToken
from langchain_core.tools import BaseTool, StructuredTool

from agent_iq.config import Settings
from agent_iq.connectors.foundry_toolbox import (
    SourceBoundPayload,
    ToolboxAuth,
    build_lazy_knowledge_tool,
    build_toolbox_client,
    load_toolbox_tools,
    toolbox_mcp_url,
)


class FakeCredential:
    def get_token(
        self,
        *scopes: str,
        claims: str | None = None,
        tenant_id: str | None = None,
        enable_cae: bool = False,
        **kwargs: Any,
    ) -> AccessToken:
        del scopes, claims, tenant_id, enable_cae, kwargs
        return AccessToken("test-token", 4_000_000_000)


class FakeToolboxClient:
    def __init__(self, tools: Sequence[BaseTool]) -> None:
        self.tools = list(tools)
        self.calls = 0

    async def get_tools(self, *, server_name: str | None = None) -> list[BaseTool]:
        del server_name
        self.calls += 1
        return self.tools


def _search(query: str) -> str:
    return f"grounded: {query}"


def tool() -> BaseTool:
    return StructuredTool.from_function(
        _search,
        name="knowledge_base_retrieve",
        description="Retrieve grounded knowledge.",
    )


def test_toolbox_url_supports_explicit_and_project_scoped_routes() -> None:
    explicit = Settings(
        toolbox_endpoint="https://example.invalid/versioned/toolbox/mcp",
        _env_file=None,
    )
    assert toolbox_mcp_url(explicit) == "https://example.invalid/versioned/toolbox/mcp"

    scoped = Settings(
        foundry_project_endpoint="https://example.services.ai.azure.com/api/projects/demo",
        azure_ai_account="example",
        azure_ai_project="demo",
        toolbox_name="knowledge-tools",
        _env_file=None,
    )
    assert toolbox_mcp_url(scoped) == (
        "https://example.services.ai.azure.com/api/projects/demo/"
        "toolboxes/knowledge-tools/mcp?api-version=v1"
    )


def test_toolbox_url_requires_project_and_name() -> None:
    with pytest.raises(ValueError, match="FOUNDRY_PROJECT_ENDPOINT"):
        toolbox_mcp_url(Settings(toolbox_name="knowledge", _env_file=None))
    with pytest.raises(ValueError, match="TOOLBOX_NAME"):
        toolbox_mcp_url(
            Settings(
                foundry_project_endpoint=(
                    "https://example.services.ai.azure.com/api/projects/demo"
                ),
                azure_ai_account="example",
                azure_ai_project="demo",
                _env_file=None,
            )
        )


def test_refreshable_auth_sets_a_token_per_request() -> None:
    issued = iter(("first", "second"))
    auth = ToolboxAuth(lambda: next(issued))

    first = httpx.Request("GET", "https://example.invalid/mcp")
    second = httpx.Request("GET", "https://example.invalid/mcp")
    list(auth.auth_flow(first))
    list(auth.auth_flow(second))

    assert first.headers["Authorization"] == "Bearer first"
    assert second.headers["Authorization"] == "Bearer second"


def test_load_tools_uses_injected_client_and_rejects_empty_toolbox() -> None:
    fake = FakeToolboxClient([tool()])
    loaded = asyncio.run(load_toolbox_tools(client=fake))

    assert [item.name for item in loaded] == ["knowledge_base_retrieve"]
    assert fake.calls == 1

    with pytest.raises(RuntimeError, match="exposed no tools"):
        asyncio.run(load_toolbox_tools(client=FakeToolboxClient([])))


def test_lazy_knowledge_tool_defers_discovery_and_caches_one_backend() -> None:
    calls: list[list[str]] = []

    async def retrieve(queries: list[str]) -> str:
        calls.append(list(queries))
        return f"grounded: {queries[0]}"

    backend = StructuredTool.from_function(
        coroutine=retrieve,
        name="knowledge_base_retrieve",
        description="Retrieve grounded knowledge.",
        args_schema={
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 1,
                }
            },
            "required": ["queries"],
        },
    )
    loads = 0

    async def loader() -> list[BaseTool]:
        nonlocal loads
        loads += 1
        return [backend]

    lazy = build_lazy_knowledge_tool(
        Settings(_env_file=None),
        loader=loader,
    )

    assert loads == 0
    assert set(lazy.args) == {"queries"}
    first = asyncio.run(lazy.ainvoke({"queries": ["first question"]}))
    second = asyncio.run(lazy.ainvoke({"queries": ["second question"]}))

    assert first == "grounded: first question"
    assert second == "grounded: second question"
    assert loads == 1
    assert calls == [["first question"], ["second question"]]


def test_lazy_knowledge_tool_rejects_incompatible_or_ambiguous_backends() -> None:
    def incompatible_backend(value: str) -> str:
        return value

    async def incompatible_loader() -> list[BaseTool]:
        return [
            StructuredTool.from_function(
                incompatible_backend,
                name="arbitrary_remote_tool",
                description="A tool without a knowledge query schema.",
            )
        ]

    incompatible = build_lazy_knowledge_tool(
        Settings(_env_file=None),
        loader=incompatible_loader,
    )
    with pytest.raises(RuntimeError, match="exactly one compatible"):
        asyncio.run(incompatible.ainvoke({"queries": ["question"]}))

    async def ambiguous_loader() -> list[BaseTool]:
        return [
            tool(),
            StructuredTool.from_function(
                _search,
                name="knowledge_base",
                description="A second knowledge tool.",
            ),
        ]

    ambiguous = build_lazy_knowledge_tool(
        Settings(_env_file=None),
        loader=ambiguous_loader,
    )
    with pytest.raises(RuntimeError, match="exactly one compatible"):
        asyncio.run(ambiguous.ainvoke({"queries": ["question"]}))


def test_lazy_knowledge_tool_restores_payload_only_from_bound_source() -> None:
    payload = SourceBoundPayload(
        source_title="VPN recovery guidance snapshot",
        start_marker="VPN_RECOVERY_JSON_START",
        end_marker="VPN_RECOVERY_JSON_END",
        payload=('VPN_RECOVERY_JSON_START\n{"population":2400}\nVPN_RECOVERY_JSON_END'),
    )

    async def grounded(query: str) -> str:
        del query
        return (
            "Guidance ready [ref_id:0]\n"
            "Source title: VPN recovery guidance snapshot [ref_id:0]\n"
            "VPN_RECOVERY_JSON_START\n"
            '{"population":1}\n'
            "VPN_RECOVERY_JSON_END"
        )

    backend = StructuredTool.from_function(
        coroutine=grounded,
        name="namespaced_employee_it_helpdesk_retrieval",
        description="Retrieve grounded knowledge.",
    )

    async def loader() -> list[BaseTool]:
        return [backend]

    lazy = build_lazy_knowledge_tool(
        Settings(_env_file=None),
        loader=loader,
        source_bound_payload=payload,
    )
    result = asyncio.run(lazy.ainvoke({"queries": ["expense readiness"]}))

    assert result.count(payload.start_marker) == 1
    assert result.count(payload.end_marker) == 1
    assert payload.payload in result
    assert '"laneId":"changed"' not in result


def test_lazy_knowledge_tool_withholds_payload_without_bound_source_evidence() -> None:
    payload = SourceBoundPayload(
        source_title="Required source",
        start_marker="PAYLOAD_START",
        end_marker="PAYLOAD_END",
        payload="PAYLOAD_START\n{}\nPAYLOAD_END",
    )

    async def ungrounded(query: str) -> str:
        return (
            f"Unsupported answer for {query} from another source [ref_id:0].\n"
            "Required source was mentioned separately without a citation."
        )

    backend = StructuredTool.from_function(
        coroutine=ungrounded,
        name="namespaced_retrieval",
        description="Retrieve grounded knowledge.",
    )

    async def loader() -> list[BaseTool]:
        return [backend]

    lazy = build_lazy_knowledge_tool(
        Settings(_env_file=None),
        loader=loader,
        source_bound_payload=payload,
    )
    with pytest.raises(RuntimeError, match="payload was not released"):
        asyncio.run(lazy.ainvoke({"queries": ["question"]}))


def test_build_toolbox_client_uses_current_mcp_adapter() -> None:
    settings = Settings(
        toolbox_endpoint="https://example.invalid/toolboxes/knowledge/mcp",
        _env_file=None,
    )

    client = build_toolbox_client(settings, FakeCredential())

    assert client.__class__.__name__ == "MultiServerMCPClient"
    connection: Any = cast(Any, client).connections["knowledge-base"]
    http = connection["httpx_client_factory"](
        timeout=httpx.Timeout(30.0),
        auth=connection["auth"],
    )
    try:
        assert http.follow_redirects is False
        assert http.trust_env is False
        tls_context = cast(Any, http)._transport._pool._ssl_context
        assert isinstance(tls_context, ssl.SSLContext)
        assert tls_context.check_hostname is True
        assert tls_context.verify_mode is ssl.CERT_REQUIRED
    finally:
        asyncio.run(http.aclose())
