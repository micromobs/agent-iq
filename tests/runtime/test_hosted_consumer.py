"""Fabrikam Consumer Products (synthetic demo) LangGraph hosted runtime tests."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool, StructuredTool

from agent_iq.catalog import load_consumer_assets
from agent_iq.config import Settings
from agent_iq.contracts import (
    VACATION_BALANCE_JSON_END,
    VACATION_BALANCE_JSON_START,
    VPN_RECOVERY_JSON_END,
    VPN_RECOVERY_JSON_START,
    parse_vacation_balance,
    parse_vpn_recovery,
)
from agent_iq.runtime import hosted_consumer


class ToolModel(GenericFakeChatModel):
    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Any | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[Any, Any]:
        del tools, tool_choice, kwargs
        return self


def model() -> ToolModel:
    return ToolModel(messages=iter([AIMessage(content="ok")]))


def _retrieve(query: str) -> str:
    return f"Grounded answer for {query} [ref_id:0]"


def _retrieve_with_title(query: str) -> str:
    return (
        f"Grounded answer for {query} [ref_id:0]\n"
        "Source title: **\u201cBrightWash Cold active system\u201d** [ref_id:0]"
    )


def _retrieve_comparison_with_titles(query: str) -> str:
    return (
        f"Grounded comparison for {query}\n"
        "BrightWash has four core enzymes [ref_id:0]\n"
        "Source title: BrightWash Cold active system [ref_id:0]\n"
        "ClearWave adds pectate lyase [ref_id:1]\n"
        "Source title: ClearWave enzyme system [ref_id:1]\n"
        "Use a governed comparison method [ref_id:2]\n"
        "Source title: Governed detergent comparison method [ref_id:2]"
    )


def _retrieve_vacation(query: str) -> str:
    del query
    document = next(
        document
        for document in load_consumer_assets().documents
        if document.document_id == "employee-vacation-balance-2026"
    )
    return f"{document.content}\nSource title: {document.title} [ref_id:0]"


def knowledge_tool() -> BaseTool:
    return StructuredTool.from_function(
        _retrieve,
        name="knowledge_base_retrieve",
        description="Retrieve grounded knowledge.",
    )


def titled_knowledge_tool() -> BaseTool:
    return StructuredTool.from_function(
        _retrieve_with_title,
        name="knowledge_base_retrieve",
        description="Retrieve grounded knowledge with exact source titles.",
    )


def comparison_knowledge_tool() -> BaseTool:
    return StructuredTool.from_function(
        _retrieve_comparison_with_titles,
        name="compare_active_systems",
        description="Compare two grounded product active systems.",
    )


def vacation_knowledge_tool() -> BaseTool:
    return StructuredTool.from_function(
        _retrieve_vacation,
        name="knowledge_base_retrieve",
        description="Retrieve the synthetic vacation balance.",
    )


def vpn_knowledge_tool(document_id: str) -> BaseTool:
    document = next(
        document
        for document in load_consumer_assets().documents
        if document.document_id == document_id
    )

    def retrieve(query: str) -> str:
        del query
        return f"{document.content}\nSource title: {document.title} [ref_id:0]"

    return StructuredTool.from_function(
        retrieve,
        name="knowledge_base_retrieve",
        description="Retrieve the VPN recovery guidance evidence.",
    )


def test_definition_and_prompt_come_from_canonical_assets() -> None:
    formulation = hosted_consumer.definition_for("consumer-formulation-rd")
    comparison = hosted_consumer.definition_for("consumer-brand-to-brand-compare")

    assert formulation.runtime.knowledge_binding_id == "consumer-product-innovation-kb"
    assert formulation.descriptor.implementation.framework == "LangGraph"
    formulation_prompt = hosted_consumer.system_prompt(formulation)
    assert "narrow domain tools" in formulation_prompt
    assert "knowledge_base_retrieve" not in formulation_prompt
    assert "smallest sufficient set" in formulation_prompt
    assert "Do not introduce adjacent products, competitors, comparisons" in formulation_prompt
    assert "exact source title" in formulation_prompt
    assert "[ref_id:n] marker" in formulation_prompt
    assert "Use compare_active_systems only when" in formulation_prompt
    assert "distinct [ref_id:n] marker for each product" in formulation_prompt
    assert "omit comparison-method evidence" in formulation_prompt
    assert "Never broaden a single-product answer" in formulation_prompt
    assert "side-by-side" in hosted_consumer.system_prompt(comparison)

    with pytest.raises(
        KeyError,
        match=r"Unknown Fabrikam Consumer Products \(synthetic demo\) agent",
    ):
        hosted_consumer.definition_for("consumer-missing")


def test_graph_compiles_offline_with_injected_model_and_tool() -> None:
    graph = hosted_consumer.create_consumer_graph(
        "consumer-formulation-rd",
        model=model(),
        tools=[knowledge_tool()],
    )

    assert graph.__class__.__name__ == "CompiledStateGraph"

    with pytest.raises(ValueError, match="at least one"):
        hosted_consumer.create_consumer_graph(
            "consumer-formulation-rd",
            model=model(),
            tools=[],
        )


def test_graph_preserves_exact_tool_source_title_in_final_answer() -> None:
    tool_call = {
        "name": "knowledge_base_retrieve",
        "args": {"query": "BrightWash cold-water enzymes"},
        "id": "call-1",
        "type": "tool_call",
    }
    source_model = ToolModel(
        messages=iter(
            [
                AIMessage(content="", tool_calls=[tool_call]),
                AIMessage(
                    content=(
                        "BrightWash uses protease, amylase, mannanase, and lipase "
                        "[ref_id:0]. Source: BrightWash cold-water liquid detergent performance."
                    )
                ),
            ]
        )
    )
    graph = hosted_consumer.create_consumer_graph(
        "consumer-formulation-rd",
        model=source_model,
        tools=[titled_knowledge_tool()],
    )

    result = graph.invoke({"messages": [HumanMessage(content="Which enzymes?")]})

    assert result["messages"][-1].content.endswith(
        "Sources:\n- BrightWash Cold active system [ref_id:0]"
    )


def test_graph_preserves_marker_when_tool_exposes_no_source_title() -> None:
    tool_call = {
        "name": "knowledge_base_retrieve",
        "args": {"query": "Demand sensing"},
        "id": "call-1",
        "type": "tool_call",
    }
    source_model = ToolModel(
        messages=iter(
            [
                AIMessage(content="", tool_calls=[tool_call]),
                AIMessage(content="Demand sensing lowers forecast error and stockouts."),
            ]
        )
    )
    graph = hosted_consumer.create_consumer_graph(
        "consumer-formulation-rd",
        model=source_model,
        tools=[knowledge_tool()],
    )

    result = graph.invoke({"messages": [HumanMessage(content="How does it help?")]})

    assert result["messages"][-1].content.endswith("Sources:\n- [ref_id:0]")


def test_graph_preserves_two_used_product_sources_and_omits_unused_method() -> None:
    tool_call = {
        "name": "compare_active_systems",
        "args": {"query": "BrightWash versus ClearWave enzymes"},
        "id": "call-1",
        "type": "tool_call",
    }
    source_model = ToolModel(
        messages=iter(
            [
                AIMessage(content="", tool_calls=[tool_call]),
                AIMessage(
                    content=(
                        "BrightWash Cold active system uses four core enzymes [ref_id:0]. "
                        "ClearWave enzyme system adds pectate lyase [ref_id:0]."
                    )
                ),
            ]
        )
    )
    graph = hosted_consumer.create_consumer_graph(
        "consumer-formulation-rd",
        model=source_model,
        tools=[comparison_knowledge_tool()],
    )

    result = graph.invoke({"messages": [HumanMessage(content="Compare the products.")]})
    answer = result["messages"][-1].content

    assert "BrightWash Cold active system" in answer
    assert "[ref_id:0]" in answer
    assert answer.endswith("Sources:\n- ClearWave enzyme system [ref_id:1]")
    assert "Governed detergent comparison method" not in answer
    assert "[ref_id:2]" not in answer


def test_graph_restores_exact_vacation_payload_from_tool_output() -> None:
    tool_call = {
        "name": "knowledge_base_retrieve",
        "args": {"query": "vacation balance"},
        "id": "call-1",
        "type": "tool_call",
    }
    source_model = ToolModel(
        messages=iter(
            [
                AIMessage(content="", tool_calls=[tool_call]),
                AIMessage(
                    content=(
                        "You have 12 vacation days remaining [ref_id:0].\n\n"
                        f"{VACATION_BALANCE_JSON_START}\n"
                        '{"vacation_days_remaining":12,"year":2026}\n'
                        f"{VACATION_BALANCE_JSON_END}"
                    )
                ),
            ]
        )
    )
    graph = hosted_consumer.create_consumer_graph(
        "employee-vacation-leave",
        model=source_model,
        tools=[vacation_knowledge_tool()],
    )

    result = graph.invoke({"messages": [HumanMessage(content="How much vacation remains?")]})
    answer = result["messages"][-1].content
    balance = parse_vacation_balance(answer)

    assert balance.remaining_days == 12
    assert answer.count(VACATION_BALANCE_JSON_START) == 1
    assert answer.count(VACATION_BALANCE_JSON_END) == 1
    assert '"vacation_days_remaining"' not in answer


def test_graph_restores_exact_vpn_payload_from_tool_output() -> None:
    tool_call = {
        "name": "knowledge_base_retrieve",
        "args": {"query": "vpn guidance"},
        "id": "call-1",
        "type": "tool_call",
    }
    source_model = ToolModel(
        messages=iter(
            [
                AIMessage(content="", tool_calls=[tool_call]),
                AIMessage(
                    content=(
                        "The guidance is ready [ref_id:0].\n\n"
                        f"{VPN_RECOVERY_JSON_START}\n"
                        '{"population":1}\n'
                        f"{VPN_RECOVERY_JSON_END}"
                    )
                ),
            ]
        )
    )
    graph = hosted_consumer.create_consumer_graph(
        "employee-it-helpdesk",
        model=source_model,
        tools=[vpn_knowledge_tool("employee-vpn-recovery-guidance")],
    )

    result = graph.invoke({"messages": [HumanMessage(content="Help now.")]})
    answer = result["messages"][-1].content
    guidance = parse_vpn_recovery(answer)

    assert guidance.agent_id == "employee-it-helpdesk"
    assert guidance.population == 2400
    assert answer.count(VPN_RECOVERY_JSON_START) == 1
    assert answer.count(VPN_RECOVERY_JSON_END) == 1
    assert '"population":1' not in answer


def test_source_bound_vpn_payload_comes_from_canonical_corpus() -> None:
    payload = hosted_consumer.source_bound_payload(
        hosted_consumer.definition_for("employee-it-helpdesk")
    )

    assert payload is not None
    assert payload.source_title == "VPN recovery guidance snapshot"
    assert payload.payload.startswith(VPN_RECOVERY_JSON_START)
    assert payload.payload.endswith(VPN_RECOVERY_JSON_END)
    assert parse_vpn_recovery(payload.payload).agent_id == "employee-it-helpdesk"


def test_foundry_model_requires_explicit_host_configuration() -> None:
    with pytest.raises(ValueError, match="FOUNDRY_PROJECT_ENDPOINT"):
        hosted_consumer.foundry_chat_model(Settings(_env_file=None))
    with pytest.raises(ValueError, match="AZURE_AI_MODEL_DEPLOYMENT_NAME"):
        hosted_consumer.foundry_chat_model(
            Settings(
                foundry_project_endpoint=(
                    "https://example.services.ai.azure.com/api/projects/demo"
                ),
                azure_ai_account="example",
                azure_ai_project="demo",
                _env_file=None,
            )
        )


def test_foundry_model_uses_managed_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    fake_model = model()

    def fake_chat_model(**kwargs: Any) -> ToolModel:
        captured.update(kwargs)
        return fake_model

    monkeypatch.setattr(hosted_consumer, "DefaultAzureCredential", FakeCredential)
    monkeypatch.setattr(hosted_consumer, "AzureAIOpenAIApiChatModel", fake_chat_model)
    settings = Settings(
        foundry_project_endpoint="https://example.services.ai.azure.com/api/projects/demo",
        azure_ai_account="example",
        azure_ai_project="demo",
        azure_model_deployment="gpt-current",
        _env_file=None,
    )

    result = hosted_consumer.foundry_chat_model(settings)

    assert result is fake_model
    assert captured["project_endpoint"] == (
        "https://example.services.ai.azure.com/api/projects/demo"
    )
    assert captured["model"] == "gpt-current"
    assert isinstance(captured["credential"], FakeCredential)


class FakeCredential:
    """Construction-only managed identity stand-in."""

    def __init__(self, **kwargs: Any) -> None:
        assert kwargs["transport"]._use_env_settings is False
        assert kwargs["redirect_policy"].allow is False


def test_hosted_graph_defers_toolbox_discovery_until_tool_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builds = 0

    def fake_lazy_tool(
        settings: Settings,
        *,
        source_bound_payload: Any,
    ) -> BaseTool:
        nonlocal builds
        builds += 1
        assert settings.toolbox_name == "knowledge"
        assert source_bound_payload is None
        return knowledge_tool()

    fake_model = model()
    monkeypatch.setattr(hosted_consumer, "build_lazy_knowledge_tool", fake_lazy_tool)
    monkeypatch.setattr(hosted_consumer, "foundry_chat_model", lambda settings: fake_model)
    settings = Settings(toolbox_name="knowledge", _env_file=None)

    graph = hosted_consumer.build_hosted_graph("consumer-formulation-rd", settings)

    assert graph.__class__.__name__ == "CompiledStateGraph"
    assert builds == 1
