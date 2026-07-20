"""Build one framework-selected consumer agent for Microsoft Foundry Hosted Agent Service."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from azure.identity import DefaultAzureCredential
from langchain.agents import create_agent
from langchain.agents.middleware import AgentState, after_model
from langchain_azure_ai.chat_models import (  # type: ignore[import-untyped]
    AzureAIOpenAIApiChatModel,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.runtime import Runtime

from agent_iq.catalog import ConsumerRuntimeAssets, load_consumer_runtime_assets
from agent_iq.catalog.assets import KnowledgeDocument, RuntimeAgentDefinition
from agent_iq.config import SapMode, Settings, get_settings
from agent_iq.connectors.entra import isolated_default_azure_credential
from agent_iq.connectors.foundry_toolbox import (
    SourceBoundPayload,
    build_lazy_knowledge_tool,
)
from agent_iq.connectors.sap import FixtureSapReader
from agent_iq.contracts import (
    VACATION_BALANCE_JSON_END,
    VACATION_BALANCE_JSON_START,
    VPN_RECOVERY_JSON_END,
    VPN_RECOVERY_JSON_START,
    AgentDescriptor,
    parse_vacation_balance,
    parse_vpn_recovery,
)

from .hero_tools import build_agent_tools

_SOURCE_TITLE = re.compile(
    r"source\s+title\s*:\s*(?P<title>.+?)\s*(?P<marker>\[ref_id:\d+\])",
    re.IGNORECASE,
)
_SOURCE_REFERENCE = re.compile(r"\[ref_id:\d+\]", re.IGNORECASE)
_SOURCE_TITLE_TRIM = " \t\r\n*`_'\"\u2018\u2019\u201c\u201d\ufffd"
_VACATION_BALANCE_BLOCK = re.compile(
    rf"{re.escape(VACATION_BALANCE_JSON_START)}.*?"
    rf"{re.escape(VACATION_BALANCE_JSON_END)}",
    re.DOTALL,
)
_VPN_RECOVERY_BLOCK = re.compile(
    rf"{re.escape(VPN_RECOVERY_JSON_START)}.*?"
    rf"{re.escape(VPN_RECOVERY_JSON_END)}",
    re.DOTALL,
)
_CONSUMER_DATA = Path(__file__).resolve().parents[1] / "data" / "consumer"


@dataclass(frozen=True, slots=True)
class HostedConsumerDefinition:
    """Canonical public descriptor paired with private runtime configuration."""

    descriptor: AgentDescriptor
    runtime: RuntimeAgentDefinition


def definition_for(
    agent_id: str,
    assets: ConsumerRuntimeAssets | None = None,
) -> HostedConsumerDefinition:
    """Resolve one canonical hosted-agent definition by stable ID."""

    runtime_assets = assets or load_consumer_runtime_assets()
    descriptors = {agent.agent_id: agent for agent in runtime_assets.catalog.agents}
    runtimes = {agent.agent_id: agent for agent in runtime_assets.runtime.agents}
    try:
        return HostedConsumerDefinition(
            descriptor=descriptors[agent_id],
            runtime=runtimes[agent_id],
        )
    except KeyError as exc:
        raise KeyError(
            f"Unknown Fabrikam Consumer Products (synthetic demo) agent ID {agent_id!r}."
        ) from exc


def system_prompt(definition: HostedConsumerDefinition) -> str:
    """Create the grounded-or-decline prompt from canonical runtime assets."""

    segment = next(
        (
            label.value
            for label in definition.descriptor.labels
            if label.key.casefold() == "segment"
        ),
        "business",
    )
    tool_instruction = (
        "Use only the provided narrow domain tools for every factual answer. "
        if definition.runtime.tools
        else "Use the Foundry IQ knowledge_base_retrieve tool for every factual answer. "
    )
    prompt = (
        f"You are {definition.descriptor.name}, a {segment} specialist. "
        f"{tool_instruction}"
        "Ground every claim only in approved tool outputs and preserve their provenance. "
        "Answer only the user's exact requested scope. Use the smallest sufficient set of "
        "tools and directly relevant source records. Do not introduce adjacent products, "
        "competitors, comparisons, or background unless the user explicitly asks for them. "
        "Cite only evidence needed for the answer. For every citation, preserve both the "
        "exact source title returned by the tool and its [ref_id:n] marker. "
        "Never invent facts, prices, figures, or source references. If the knowledge base "
        "or governed system does not contain the answer, say so plainly and identify the "
        "missing information."
    )
    if definition.runtime.prompt_addendum:
        return f"{prompt}\n\n{definition.runtime.prompt_addendum}"
    return prompt


def source_title_citations(texts: Sequence[str]) -> tuple[tuple[str, str], ...]:
    """Extract unique source-title and reference-marker pairs from tool text."""

    citations: list[tuple[str, str]] = []
    for text in texts:
        for match in _SOURCE_TITLE.finditer(text):
            citation = (
                match.group("title").strip(_SOURCE_TITLE_TRIM),
                match.group("marker"),
            )
            if citation[0] and citation not in citations:
                citations.append(citation)
    return tuple(citations)


def append_missing_source_citations(
    answer: str,
    citations: Sequence[tuple[str, str]],
) -> str:
    """Append exact tool provenance that the model omitted from its final answer."""

    folded = answer.casefold()
    missing = [
        (title, marker)
        for title, marker in citations
        if title.casefold() not in folded or marker.casefold() not in folded
    ]
    if not missing:
        return answer
    sources = "\n".join(f"- {title} {marker}" for title, marker in missing)
    return f"{answer.rstrip()}\n\nSources:\n{sources}"


def citations_used_by_answer(
    answer: str,
    citations: Sequence[tuple[str, str]],
) -> tuple[tuple[str, str], ...]:
    """Keep title/marker pairs only when the answer already uses either half."""

    folded = answer.casefold()
    return tuple(
        (title, marker)
        for title, marker in citations
        if title.casefold() in folded or marker.casefold() in folded
    )


def source_reference_markers(texts: Sequence[str]) -> tuple[str, ...]:
    """Extract unique source markers from grounded tool text."""

    markers: list[str] = []
    for text in texts:
        for match in _SOURCE_REFERENCE.finditer(text):
            marker = match.group(0)
            if marker.casefold() not in {item.casefold() for item in markers}:
                markers.append(marker)
    return tuple(markers)


def append_missing_source_references(
    answer: str,
    markers: Sequence[str],
) -> str:
    """Retain exact tool reference markers even when no source title is exposed."""

    folded = answer.casefold()
    missing = [marker for marker in markers if marker.casefold() not in folded]
    if not missing:
        return answer
    references = "\n".join(f"- {marker}" for marker in missing)
    return f"{answer.rstrip()}\n\nSources:\n{references}"


def preserve_vacation_balance_payload(answer: str, tool_texts: Sequence[str]) -> str:
    """Restore the exact validated balance block from authoritative tool output."""

    source_blocks = {
        match.group(0) for text in tool_texts for match in _VACATION_BALANCE_BLOCK.finditer(text)
    }
    if len(source_blocks) != 1:
        return answer
    source_block = next(iter(source_blocks))
    try:
        parse_vacation_balance(source_block)
    except ValueError:
        return answer
    if (
        answer.count(VACATION_BALANCE_JSON_START) == 1
        and answer.count(VACATION_BALANCE_JSON_END) == 1
        and source_block in answer
    ):
        return answer

    without_blocks = _VACATION_BALANCE_BLOCK.sub("", answer)
    without_markers = without_blocks.replace(VACATION_BALANCE_JSON_START, "").replace(
        VACATION_BALANCE_JSON_END,
        "",
    )
    human_answer = without_markers.strip()
    if not human_answer:
        return source_block
    return f"{human_answer}\n\n{source_block}"


def _preserve_marked_payload(
    answer: str,
    tool_texts: Sequence[str],
    *,
    block: re.Pattern[str],
    start_marker: str,
    end_marker: str,
    parser: Callable[[str], object],
) -> str:
    """Restore one exact validated machine payload from authoritative tool output."""

    source_blocks = {match.group(0) for text in tool_texts for match in block.finditer(text)}
    if len(source_blocks) != 1:
        return answer
    source_block = next(iter(source_blocks))
    try:
        parser(source_block)
    except ValueError:
        return answer
    if answer.count(start_marker) == 1 and answer.count(end_marker) == 1 and source_block in answer:
        return answer

    without_blocks = block.sub("", answer)
    without_markers = without_blocks.replace(start_marker, "").replace(end_marker, "")
    human_answer = without_markers.strip()
    if not human_answer:
        return source_block
    return f"{human_answer}\n\n{source_block}"


def preserve_vpn_recovery_payload(answer: str, tool_texts: Sequence[str]) -> str:
    """Restore the exact aggregate VPN guidance block from authoritative tool output."""

    return _preserve_marked_payload(
        answer,
        tool_texts,
        block=_VPN_RECOVERY_BLOCK,
        start_marker=VPN_RECOVERY_JSON_START,
        end_marker=VPN_RECOVERY_JSON_END,
        parser=parse_vpn_recovery,
    )


def source_bound_payload(
    definition: HostedConsumerDefinition,
) -> SourceBoundPayload | None:
    """Load one local machine contract that is hash-bound to the deployed corpus."""

    corpus_path = _CONSUMER_DATA / "corpora" / f"{definition.descriptor.agent_id}.jsonl"
    if not corpus_path.is_file():
        return None
    documents = [
        KnowledgeDocument.model_validate_json(line)
        for line in corpus_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    candidates: list[SourceBoundPayload] = []
    for document in documents:
        vacation_blocks = [
            match.group(0) for match in _VACATION_BALANCE_BLOCK.finditer(document.content)
        ]
        vpn_blocks = [match.group(0) for match in _VPN_RECOVERY_BLOCK.finditer(document.content)]
        for block in vacation_blocks:
            parse_vacation_balance(block)
            candidates.append(
                SourceBoundPayload(
                    source_title=document.title,
                    start_marker=VACATION_BALANCE_JSON_START,
                    end_marker=VACATION_BALANCE_JSON_END,
                    payload=block,
                )
            )
        for block in vpn_blocks:
            guidance = parse_vpn_recovery(block)
            if guidance.agent_id != definition.descriptor.agent_id:
                raise ValueError("VPN source payload agent ID does not match its runtime.")
            if guidance.source_title != document.title:
                raise ValueError("VPN source payload title does not match its document.")
            candidates.append(
                SourceBoundPayload(
                    source_title=document.title,
                    start_marker=VPN_RECOVERY_JSON_START,
                    end_marker=VPN_RECOVERY_JSON_END,
                    payload=block,
                )
            )
    if len(candidates) != 1:
        raise ValueError(
            f"Expected exactly one canonical machine payload for "
            f"{definition.descriptor.agent_id!r}."
        )
    return candidates[0]


def _source_title_citations(messages: Sequence[Any]) -> tuple[tuple[str, str], ...]:
    return source_title_citations(
        [message.text for message in messages if isinstance(message, ToolMessage)]
    )


def _preserve_tool_source_titles(
    state: AgentState[Any],
    runtime: Runtime[Any],
) -> dict[str, Any] | None:
    del runtime
    messages = state["messages"]
    if not messages or not isinstance(messages[-1], AIMessage):
        return None
    final = messages[-1]
    if final.tool_calls:
        return None
    tool_texts = [message.text for message in messages if isinstance(message, ToolMessage)]
    citations = _source_title_citations(messages)
    references = source_reference_markers(tool_texts)
    answer = final.text
    updated_answer = preserve_vacation_balance_payload(answer, tool_texts)
    updated_answer = preserve_vpn_recovery_payload(updated_answer, tool_texts)
    selected_citations = citations_used_by_answer(updated_answer, citations)
    updated_answer = append_missing_source_citations(updated_answer, selected_citations)
    if not citations:
        updated_answer = append_missing_source_references(updated_answer, references)
    if updated_answer == answer:
        return None
    updated = final.model_copy(update={"content": updated_answer})
    return {"messages": [updated]}


SOURCE_TITLE_MIDDLEWARE = after_model(_preserve_tool_source_titles)


def foundry_chat_model(settings: Settings | None = None) -> BaseChatModel:
    """Create the current first-party LangChain model using managed identity."""

    active_settings = settings or get_settings()
    if active_settings.foundry_project_endpoint is None:
        raise ValueError("FOUNDRY_PROJECT_ENDPOINT is required by the hosted chat model.")
    if not active_settings.azure_model_deployment:
        raise ValueError("AZURE_AI_MODEL_DEPLOYMENT_NAME is required by the hosted chat model.")
    return cast(
        BaseChatModel,
        AzureAIOpenAIApiChatModel(
            project_endpoint=str(active_settings.foundry_project_endpoint),
            credential=isolated_default_azure_credential(
                credential_factory=DefaultAzureCredential,
            ),
            model=active_settings.azure_model_deployment,
        ),
    )


def create_consumer_graph(
    agent_id: str,
    *,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    assets: ConsumerRuntimeAssets | None = None,
) -> Any:
    """Compile a LangGraph agent from injected dependencies without network access."""

    if not tools:
        raise ValueError("A hosted consumer agent requires at least one Foundry IQ tool.")
    definition = definition_for(agent_id, assets)
    if definition.descriptor.implementation.framework != "LangGraph":
        raise ValueError(f"{agent_id!r} is not configured for LangGraph.")
    return create_agent(
        model,
        tools=list(tools),
        system_prompt=system_prompt(definition),
        middleware=[SOURCE_TITLE_MIDDLEWARE],
        name=agent_id.replace("-", "_"),
    )


def build_hosted_graph(agent_id: str, settings: Settings | None = None) -> Any:
    """Build the framework declared in canonical metadata behind one host shape."""

    active_settings = settings or get_settings()
    assets = load_consumer_runtime_assets()
    definition = definition_for(agent_id, assets)
    if definition.descriptor.implementation.framework == "PydanticAI":
        from .pydantic_consumer import build_pydantic_hosted_graph

        return build_pydantic_hosted_graph(agent_id, active_settings)
    if definition.descriptor.implementation.framework != "LangGraph":
        raise ValueError(
            f"Unsupported hosted framework {definition.descriptor.implementation.framework!r}."
        )
    toolbox_tools = [
        build_lazy_knowledge_tool(
            active_settings,
            source_bound_payload=source_bound_payload(definition),
        )
    ]
    sap_reader = (
        FixtureSapReader(assets.sap_fixtures)
        if active_settings.sap_mode is SapMode.FIXTURE
        else None
    )
    tools = build_agent_tools(
        definition.runtime,
        toolbox_tools,
        sap_reader=sap_reader,
    )
    return create_consumer_graph(
        agent_id,
        model=foundry_chat_model(active_settings),
        tools=tools,
        assets=assets,
    )


def build_hosted_server(agent_id: str, settings: Settings | None = None) -> Any:
    """Build the framework-specific Responses host boundary."""

    definition = definition_for(agent_id)
    if definition.descriptor.implementation.framework == "PydanticAI":
        from .pydantic_consumer import build_pydantic_hosted_server

        return build_pydantic_hosted_server(agent_id, settings)
    from langchain_azure_ai.agents.hosting import (  # type: ignore[import-untyped]
        ResponsesHostServer,
    )

    return ResponsesHostServer(build_hosted_graph(agent_id, settings))
