"""Probe the public MCP handshake without invoking any consumer agent."""

from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta
from urllib.parse import urlsplit, urlunsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import AnyUrl

from agent_iq.api.mcp import MCP_TOOLS
from agent_iq.api.mcp_apps import (
    EMPLOYEE_OPERATIONS_RESOURCE_URI,
    FORMATION_RESOURCE_URI,
    HISTORICAL_RESOURCE_TEMPLATE_URI,
    LEGACY_VACATION_RING_RESOURCE_URI,
    VACATION_RING_RESOURCE_URI,
)


def _mcp_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/mcp"
    ):
        raise argparse.ArgumentTypeError("MCP URL must be a credential-free HTTPS /mcp route.")
    return urlunsplit(("https", parsed.hostname.casefold(), "/mcp", "", ""))


async def probe(mcp_url: str, timeout_seconds: float) -> None:
    timeout = httpx.Timeout(timeout_seconds)
    async with (
        httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as http,
        streamable_http_client(mcp_url, http_client=http) as (read_stream, write_stream, _),
        ClientSession(
            read_stream,
            write_stream,
            read_timeout_seconds=timedelta(seconds=timeout_seconds),
        ) as session,
    ):
        initialized = await session.initialize()
        tools = await session.list_tools()
        resources = await session.list_resources()
        templates = await session.list_resource_templates()
        vacation = await session.read_resource(AnyUrl(VACATION_RING_RESOURCE_URI))
        legacy_vacation = await session.read_resource(AnyUrl(LEGACY_VACATION_RING_RESOURCE_URI))
        historical = await session.read_resource(AnyUrl("ui://agent-iq/retired-widget.html"))

    actual_tools = {tool.name for tool in tools.tools}
    expected_tools = set(MCP_TOOLS)
    if actual_tools != expected_tools:
        raise RuntimeError(
            "MCP tool contract mismatch: "
            f"missing={sorted(expected_tools - actual_tools)}, "
            f"unexpected={sorted(actual_tools - expected_tools)}"
        )
    expected_resources = {
        FORMATION_RESOURCE_URI,
        VACATION_RING_RESOURCE_URI,
        LEGACY_VACATION_RING_RESOURCE_URI,
        EMPLOYEE_OPERATIONS_RESOURCE_URI,
    }
    actual_resources = {str(resource.uri) for resource in resources.resources}
    if not expected_resources <= actual_resources:
        raise RuntimeError(
            f"MCP resource contract is incomplete: {sorted(expected_resources - actual_resources)}"
        )
    actual_templates = {str(template.uriTemplate) for template in templates.resourceTemplates}
    if HISTORICAL_RESOURCE_TEMPLATE_URI not in actual_templates:
        raise RuntimeError("MCP historical resource template is unavailable.")
    vacation_text = getattr(vacation.contents[0], "text", "")
    if not vacation_text or vacation_text != getattr(legacy_vacation.contents[0], "text", ""):
        raise RuntimeError("The legacy vacation resource does not resolve to the current widget.")
    if "Historical Agent IQ experience" not in getattr(historical.contents[0], "text", ""):
        raise RuntimeError("The retired-resource compatibility view is unavailable.")
    if not str(initialized.protocolVersion):
        raise RuntimeError("MCP initialize returned no protocol version.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mcp-url", required=True, type=_mcp_url)
    parser.add_argument("--timeout-seconds", type=float, default=60)
    arguments = parser.parse_args()
    asyncio.run(probe(arguments.mcp_url, arguments.timeout_seconds))
    print("MCP contract probe: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
