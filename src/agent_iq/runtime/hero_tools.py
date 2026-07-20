"""Build narrow LangChain tools from canonical Agent IQ runtime metadata."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from time import perf_counter
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool, ToolException
from pydantic import BaseModel, ConfigDict, Field, create_model

from agent_iq.catalog.assets import RuntimeAgentDefinition, RuntimeToolDefinition
from agent_iq.connectors.sap import SapReader
from agent_iq.contracts import ErrorCode, SapSourceMode, ToolBackend, ToolParameterType
from agent_iq.errors import AgentIqOperationError
from agent_iq.runtime.telemetry import (
    LoggingToolTelemetry,
    ToolTelemetryEvent,
    ToolTelemetrySink,
)

_FIXTURE_WARNING = "DETERMINISTIC FIXTURE - NOT LIVE SAP"


class _ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def build_agent_tools(
    definition: RuntimeAgentDefinition,
    toolbox_tools: list[BaseTool],
    *,
    sap_reader: SapReader | None = None,
    telemetry: ToolTelemetrySink | None = None,
) -> list[BaseTool]:
    """Replace the opaque KB operation with metadata-defined domain tools."""

    specifications = definition.tools
    if not specifications:
        return toolbox_tools
    needs_knowledge = any(
        specification.descriptor.backend is ToolBackend.FOUNDRY_IQ
        for specification in specifications
    )
    if needs_knowledge and len(toolbox_tools) != 1:
        raise ValueError("Hero tools require exactly one Foundry IQ retrieval backend.")
    if (
        any(specification.descriptor.backend is ToolBackend.SAP for specification in specifications)
        and sap_reader is None
    ):
        raise ValueError("SAP-backed hero tools require an explicitly configured reader.")
    knowledge_tool = toolbox_tools[0] if needs_knowledge else None
    telemetry_sink = telemetry or LoggingToolTelemetry()
    return [
        _build_tool(
            specification,
            agent_id=definition.agent_id,
            knowledge_tool=knowledge_tool,
            sap_reader=sap_reader,
            telemetry=telemetry_sink,
        )
        for specification in specifications
    ]


def _build_tool(
    specification: RuntimeToolDefinition,
    *,
    agent_id: str,
    knowledge_tool: BaseTool | None,
    sap_reader: SapReader | None,
    telemetry: ToolTelemetrySink,
) -> BaseTool:
    arguments_model = _arguments_model(specification)

    async def invoke(**raw_arguments: Any) -> str:
        started = perf_counter()
        status = "failed"
        source_mode: str | None = None
        try:
            arguments = validate_tool_arguments(specification, raw_arguments)
            if specification.descriptor.backend is ToolBackend.FOUNDRY_IQ:
                if knowledge_tool is None:
                    raise RuntimeError("The Foundry IQ retrieval backend is unavailable.")
                query = specification.query_template.format(
                    **{key: _format_value(value) for key, value in arguments.items()}
                )
                result = await knowledge_tool.ainvoke(
                    _knowledge_backend_arguments(knowledge_tool, query)
                )
                status = "completed"
                return _knowledge_result_text(result)
            if sap_reader is None or specification.sap_resource is None:
                raise RuntimeError(
                    "The governed SAP reader is unavailable; no fallback data was used."
                )
            result = sap_reader.read(
                specification.sap_resource,
                arguments,
                authorization_scope=specification.descriptor.authorization_scopes[0],
            )
            source_mode = result.source_mode.value
            prefix = f"{_FIXTURE_WARNING}\n" if result.source_mode is SapSourceMode.FIXTURE else ""
            status = "completed"
            return f"{prefix}{result.model_dump_json(by_alias=True)}"
        except AgentIqOperationError as exc:
            if exc.error.code not in {
                ErrorCode.EVIDENCE_UNAVAILABLE,
                ErrorCode.INVALID_REQUEST,
            }:
                raise
            raise ToolException(
                f"Governed tool request failed ({exc.error.code.value}). "
                "Try another approved tool or state what information is missing; "
                "do not invent a record."
            ) from exc
        finally:
            policy = specification.descriptor.telemetry
            duration_ms = (
                round((perf_counter() - started) * 1000) if policy.capture_latency else None
            )
            await telemetry.emit(
                ToolTelemetryEvent.create(
                    agent_id=agent_id,
                    tool_id=specification.descriptor.tool_id,
                    backend=specification.descriptor.backend.value,
                    status=status,
                    duration_ms=duration_ms,
                    cost_units=None,
                    cost_capture_status="unavailable" if policy.capture_cost else "disabled",
                    source_mode=source_mode,
                )
            )

    return StructuredTool.from_function(
        coroutine=invoke,
        name=specification.descriptor.tool_id,
        description=specification.descriptor.description,
        args_schema=arguments_model,
        handle_tool_error=True,
    )


def _arguments_model(specification: RuntimeToolDefinition) -> type[BaseModel]:
    fields: dict[str, Any] = {}
    for parameter in specification.descriptor.parameters:
        value_type: type[str] | type[date]
        value_type = date if parameter.value_type is ToolParameterType.DATE else str
        default = ... if parameter.required else None
        annotation: Any = value_type if parameter.required else value_type | None
        fields[parameter.parameter_id] = (
            annotation,
            Field(default=default, description=parameter.description),
        )
    model_name = "".join(part.capitalize() for part in specification.descriptor.tool_id.split("_"))
    return create_model(f"{model_name}Input", __base__=_ToolArguments, **fields)


def validate_tool_arguments(
    specification: RuntimeToolDefinition,
    arguments: dict[str, object],
) -> dict[str, object]:
    """Validate a candidate tool call with the exact hosted-runtime schema."""

    return _arguments_model(specification).model_validate(arguments).model_dump()


def _format_value(value: object) -> str:
    return value.isoformat() if isinstance(value, date) else str(value)


def _knowledge_backend_arguments(tool: BaseTool, query: str) -> dict[str, object]:
    if "queries" in tool.args:
        return {"queries": [query]}
    if "query" in tool.args:
        return {"query": query}
    raise RuntimeError(
        "The Foundry IQ retrieval backend does not advertise a supported query schema."
    )


def _knowledge_result_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    blocks: Sequence[Any]
    if isinstance(result, Mapping):
        blocks = (result,)
    elif isinstance(result, Sequence) and not isinstance(result, (bytes, bytearray)):
        blocks = result
    else:
        return str(result)
    texts = [
        str(block["text"]).strip()
        for block in blocks
        if isinstance(block, Mapping)
        and block.get("type") == "text"
        and str(block.get("text") or "").strip()
    ]
    return "\n".join(texts) if texts else str(result)
