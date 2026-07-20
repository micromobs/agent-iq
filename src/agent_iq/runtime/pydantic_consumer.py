"""PydanticAI hero runtime adapted to the Foundry Responses host boundary."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib.parse import urlsplit, urlunsplit

import httpx
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential
from langchain_azure_ai.agents.hosting import ResponsesHostServer  # type: ignore[import-untyped]
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, MessagesState, StateGraph
from openai import AsyncOpenAI, DefaultAsyncHttpxClient
from pydantic import BaseModel, Field, SecretStr, TypeAdapter, ValidationError
from pydantic_ai import Agent, RunContext, ToolDefinition
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool
from pydantic_core import SchemaValidator, core_schema

from agent_iq.catalog import ConsumerRuntimeAssets, load_consumer_runtime_assets
from agent_iq.config import (
    SapMode,
    Settings,
    bind_artifact_channel,
    canonicalize_azure_openai_endpoint,
    get_settings,
)
from agent_iq.connectors.entra import (
    isolated_async_default_azure_credential,
    isolated_default_azure_credential,
)
from agent_iq.connectors.foundry_toolbox import build_lazy_knowledge_tool
from agent_iq.connectors.sap import FixtureSapReader
from agent_iq.contracts import (
    ArtifactChannelReference,
    ArtifactKind,
    BusinessArtifact,
    DemandScenario,
    RegulatoryDecision,
    SapChangeProposal,
    SupplyCommitment,
)
from agent_iq.contracts.base import ContractModel, NonEmptyText, Timestamp
from agent_iq.publication import (
    PUBLICATION_CONTEXT_MESSAGE_KEY,
    PUBLICATION_TICKET_MESSAGE_KEY,
    ArtifactPublicationContext,
    decode_publication_context_id,
    extract_publication_context,
    extract_publication_ticket,
    publication_context_marker_present,
    publication_ticket_marker_present,
    validate_publication_ticket,
)
from agent_iq.runtime.hero_tools import build_agent_tools
from agent_iq.tls import hosted_server_tls_context

from .hosted_consumer import (
    HostedConsumerDefinition,
    append_missing_source_citations,
    append_missing_source_references,
    definition_for,
    preserve_vacation_balance_payload,
    preserve_vpn_recovery_payload,
    source_bound_payload,
    source_reference_markers,
    source_title_citations,
    system_prompt,
)

PYDANTIC_AI_FRAMEWORK = "PydanticAI"
AZURE_COGNITIVE_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"
AZURE_AI_SCOPE = "https://ai.azure.com/.default"
PUBLISH_ARTIFACT_TOOL = "publish_typed_artifact"
_BUSINESS_ARTIFACT_ADAPTER: TypeAdapter[BusinessArtifact] = TypeAdapter(BusinessArtifact)
_ARTIFACT_MODELS: Mapping[ArtifactKind, type[BaseModel]] = {
    ArtifactKind.DEMAND_SCENARIO: DemandScenario,
    ArtifactKind.REGULATORY_DECISION: RegulatoryDecision,
    ArtifactKind.SAP_CHANGE_PROPOSAL: SapChangeProposal,
    ArtifactKind.SUPPLY_COMMITMENT: SupplyCommitment,
}
logger = logging.getLogger(__name__)


class PydanticHeroRequest(ContractModel):
    """Strict input presented to a PydanticAI hero run."""

    prompt: NonEmptyText


class PydanticHeroOutput(ContractModel):
    """Strict human-readable output retained by the native A2A text boundary."""

    answer: NonEmptyText = Field(
        description="Grounded answer with every source marker and fixture warning preserved."
    )


@dataclass(frozen=True, slots=True)
class PydanticInvocationContext:
    """Trusted publication state injected outside model-visible arguments."""

    invocation_id: str | None = None
    correlation_id: str | None = None
    publication_ticket: SecretStr | None = None

    def __post_init__(self) -> None:
        has_ticket = self.publication_ticket is not None
        has_coordinates = self.invocation_id is not None or self.correlation_id is not None
        if has_ticket == has_coordinates:
            raise ValueError(
                "Artifact publication context requires either a ticket or invocation coordinates."
            )
        if has_ticket:
            object.__setattr__(
                self,
                "publication_ticket",
                validate_publication_ticket(cast(SecretStr, self.publication_ticket)),
            )
            return
        if self.invocation_id is None or self.correlation_id is None:
            raise ValueError("Artifact publication context requires both invocation coordinates.")
        coordinates = ArtifactPublicationContext(
            invocation_id=self.invocation_id,
            correlation_id=self.correlation_id,
        )
        object.__setattr__(self, "invocation_id", coordinates.invocation_id)
        object.__setattr__(self, "correlation_id", coordinates.correlation_id)


class _ArtifactTicketClaimResponse(ContractModel):
    publication_ticket: SecretStr
    expires_at: Timestamp


class PydanticArtifactPublisher(Protocol):
    """Authenticated companion-channel publisher used by the synthetic tool."""

    async def publish(
        self,
        *,
        context: PydanticInvocationContext,
        artifact: BusinessArtifact,
    ) -> ArtifactChannelReference: ...


class ManagedIdentityArtifactPublisher:
    """Publish artifacts to Agent IQ using the hosted agent's managed identity."""

    def __init__(
        self,
        endpoint: str,
        companion_application_id: str,
        companion_tenant_id: str,
        *,
        token_scope: str | None = None,
        credential: Any | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._companion_application_id = companion_application_id
        self._companion_tenant_id = companion_tenant_id
        self._token_scope = token_scope
        self._credential = credential
        self._transport = transport

    async def publish(
        self,
        *,
        context: PydanticInvocationContext,
        artifact: BusinessArtifact,
    ) -> ArtifactChannelReference:
        binding = bind_artifact_channel(
            self._endpoint,
            self._companion_application_id,
            self._companion_tenant_id,
            token_scope=self._token_scope,
        )
        credential = self._credential or isolated_async_default_azure_credential(
            credential_factory=AsyncDefaultAzureCredential,
        )
        token = await credential.get_token(binding.token_scope)
        authorization = f"Bearer {token.token}"
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30),
            transport=self._transport,
            verify=hosted_server_tls_context(),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            publication_ticket = context.publication_ticket
            if publication_ticket is None:
                if context.invocation_id is None or context.correlation_id is None:
                    raise ValueError("Artifact publication claim coordinates are unavailable.")
                claim_response = await client.post(
                    binding.claim_endpoint,
                    headers={"Authorization": authorization},
                    json={
                        "invocationId": context.invocation_id,
                        "correlationId": context.correlation_id,
                    },
                )
                if claim_response.status_code in {401, 403}:
                    raise PermissionError("Agent IQ rejected artifact publisher identity.")
                try:
                    claim_response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise RuntimeError(
                        "Agent IQ artifact ticket claim failed with "
                        f"HTTP {claim_response.status_code}."
                    ) from exc
                try:
                    claim = _ArtifactTicketClaimResponse.model_validate(claim_response.json())
                except ValueError as exc:
                    raise RuntimeError(
                        "Agent IQ artifact ticket claim returned an invalid response."
                    ) from exc
                publication_ticket = validate_publication_ticket(claim.publication_ticket)
            response = await client.post(
                binding.publish_endpoint,
                headers={
                    "Authorization": authorization,
                    "X-Agent-IQ-Publication-Ticket": (publication_ticket.get_secret_value()),
                },
                json={"artifact": artifact.model_dump(mode="json", by_alias=True)},
            )
        if response.status_code in {401, 403}:
            raise PermissionError("Agent IQ rejected artifact publisher identity.")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Agent IQ artifact publication failed with HTTP {response.status_code}."
            ) from exc
        return ArtifactChannelReference.model_validate(response.json())


@dataclass(frozen=True, slots=True)
class PydanticHeroDependencies:
    """Per-run dependencies rather than module-global tool state."""

    agent_id: str
    tools: Mapping[str, BaseTool]
    allowed_artifact_kinds: tuple[ArtifactKind, ...] = ()
    artifact_publisher: PydanticArtifactPublisher | None = None
    invocation_context: PydanticInvocationContext | None = None


class LangChainToolset(AbstractToolset[PydanticHeroDependencies]):
    """Expose validated LangChain/Foundry toolbox adapters to PydanticAI."""

    @property
    def id(self) -> str:
        return "agent-iq-domain-tools"

    async def get_tools(
        self,
        ctx: RunContext[PydanticHeroDependencies],
    ) -> dict[str, ToolsetTool[PydanticHeroDependencies]]:
        result: dict[str, ToolsetTool[PydanticHeroDependencies]] = {}
        for name, tool in ctx.deps.tools.items():
            input_model = tool.get_input_schema()
            if issubclass(input_model, BaseModel):
                parameters_schema = input_model.model_json_schema(by_alias=True)
            else:
                parameters_schema = input_model.schema(by_alias=True)
            result[name] = ToolsetTool(
                toolset=self,
                tool_def=ToolDefinition(
                    name=name,
                    description=tool.description,
                    parameters_json_schema=parameters_schema,
                    strict=True,
                ),
                max_retries=1,
                args_validator=_DICT_VALIDATOR,
            )
        if ctx.deps.artifact_publisher is not None:
            if ctx.deps.invocation_context is None:
                raise ValueError("Artifact publication requires trusted invocation coordinates.")
            if not ctx.deps.allowed_artifact_kinds:
                raise ValueError("Artifact publication requires at least one allowed kind.")
            result[PUBLISH_ARTIFACT_TOOL] = ToolsetTool(
                toolset=self,
                tool_def=ToolDefinition(
                    name=PUBLISH_ARTIFACT_TOOL,
                    description=(
                        "Publish one validated business artifact through the authenticated "
                        "invocation-bound companion channel. The invocation coordinates are "
                        "injected by the runtime; never put artifact JSON in the answer."
                    ),
                    parameters_json_schema=_artifact_tool_schema(ctx.deps.allowed_artifact_kinds),
                    strict=False,
                ),
                max_retries=1,
                args_validator=_DICT_VALIDATOR,
            )
        return result

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[PydanticHeroDependencies],
        tool: ToolsetTool[PydanticHeroDependencies],
    ) -> Any:
        del tool
        if name == PUBLISH_ARTIFACT_TOOL:
            publisher = ctx.deps.artifact_publisher
            invocation_context = ctx.deps.invocation_context
            if publisher is None or invocation_context is None:
                raise ValueError("Artifact publication is unavailable for this invocation.")
            try:
                artifact = _BUSINESS_ARTIFACT_ADAPTER.validate_python(tool_args.get("artifact"))
            except ValidationError as exc:
                raise ValueError("The business artifact failed strict validation.") from exc
            if artifact.kind not in ctx.deps.allowed_artifact_kinds:
                raise ValueError(
                    f"Artifact kind {artifact.kind.value!r} is not allowed for this agent."
                )
            reference = await publisher.publish(
                context=invocation_context,
                artifact=artifact,
            )
            return reference.model_dump(mode="json", by_alias=True)
        try:
            selected = ctx.deps.tools[name]
        except KeyError as exc:
            raise ValueError(f"Unknown PydanticAI tool {name!r}.") from exc
        return await selected.ainvoke(tool_args)


_DICT_VALIDATOR = SchemaValidator(
    core_schema.dict_schema(
        keys_schema=core_schema.str_schema(),
        strict=True,
    )
)


def pydantic_model(settings: Settings | None = None) -> OpenAIResponsesModel:
    """Create a PydanticAI Responses model using only managed identity."""

    active_settings = settings or get_settings()
    if not active_settings.azure_model_deployment:
        raise ValueError("AZURE_AI_MODEL_DEPLOYMENT_NAME is required by the PydanticAI model.")
    if active_settings.azure_openai_endpoint is not None:
        account = active_settings.azure_ai_account
        if account is None:
            raise ValueError(
                "AZURE_AI_ACCOUNT is required when AZURE_OPENAI_ENDPOINT is configured."
            )
        origin = canonicalize_azure_openai_endpoint(
            str(active_settings.azure_openai_endpoint),
            expected_account=account,
        )
        endpoint = _openai_v1_base_url(origin)
        token_scope = AZURE_COGNITIVE_SERVICES_SCOPE
    elif active_settings.foundry_project_endpoint is not None:
        endpoint = _foundry_project_v1_base_url(str(active_settings.foundry_project_endpoint))
        token_scope = AZURE_AI_SCOPE
    else:
        raise ValueError(
            "AZURE_OPENAI_ENDPOINT or FOUNDRY_PROJECT_ENDPOINT is required by the PydanticAI model."
        )
    credential = isolated_default_azure_credential(
        credential_factory=DefaultAzureCredential,
    )
    token_provider = get_bearer_token_provider(
        credential,
        token_scope,
    )

    async def async_token_provider() -> str:
        return token_provider()

    client = AsyncOpenAI(
        base_url=endpoint,
        api_key=async_token_provider,
        http_client=DefaultAsyncHttpxClient(
            verify=hosted_server_tls_context(),
            follow_redirects=False,
            trust_env=False,
        ),
    )
    return OpenAIResponsesModel(
        active_settings.azure_model_deployment,
        provider=OpenAIProvider(openai_client=client),
    )


def create_pydantic_agent(
    agent_id: str,
    *,
    model: Model,
    tools: Sequence[BaseTool],
    assets: ConsumerRuntimeAssets | None = None,
    artifact_publisher: PydanticArtifactPublisher | None = None,
    invocation_context: PydanticInvocationContext | None = None,
) -> tuple[
    Agent[PydanticHeroDependencies, PydanticHeroOutput],
    PydanticHeroDependencies,
]:
    """Create a typed PydanticAI agent and its invocation dependencies."""

    if not tools:
        raise ValueError("A hosted PydanticAI hero requires at least one domain tool.")
    definition = definition_for(agent_id, assets)
    _require_pydantic_definition(definition)
    by_name = {tool.name: tool for tool in tools}
    if len(by_name) != len(tools):
        raise ValueError("PydanticAI hero tool names must be unique.")
    dependencies = PydanticHeroDependencies(
        agent_id=agent_id,
        tools=by_name,
        allowed_artifact_kinds=definition.runtime.artifact_output_kinds,
        artifact_publisher=artifact_publisher,
        invocation_context=invocation_context,
    )
    if artifact_publisher is not None and not dependencies.allowed_artifact_kinds:
        raise ValueError(f"{agent_id!r} does not declare a typed artifact output kind.")
    if (artifact_publisher is None) != (invocation_context is None):
        raise ValueError("Artifact publisher and invocation context must be configured together.")
    publication_instruction = (
        " When the request needs a reusable structured business result, call "
        f"{PUBLISH_ARTIFACT_TOOL} exactly once after grounding it. Never place the "
        "artifact JSON or invocation coordinates in the human-readable answer."
        if artifact_publisher is not None
        else ""
    )
    agent = Agent(
        model,
        output_type=PydanticHeroOutput,
        instructions=f"{system_prompt(definition)}{publication_instruction}",
        deps_type=PydanticHeroDependencies,
        name=agent_id.replace("-", "_"),
        toolsets=[LangChainToolset()],
        model_settings=OpenAIResponsesModelSettings(openai_reasoning_effort="low"),
    )
    return agent, dependencies


def create_pydantic_graph(
    agent_id: str,
    *,
    model: Model,
    tools: Sequence[BaseTool],
    assets: ConsumerRuntimeAssets | None = None,
    artifact_publisher: PydanticArtifactPublisher | None = None,
    invocation_context: PydanticInvocationContext | None = None,
) -> Any:
    """Adapt a PydanticAI agent to the LangGraph shape required by Foundry hosting."""

    if invocation_context is not None and artifact_publisher is None:
        raise ValueError("Artifact publisher and invocation context must be configured together.")
    base_agent, base_dependencies = create_pydantic_agent(
        agent_id,
        model=model,
        tools=tools,
        assets=assets,
    )

    async def invoke(state: MessagesState) -> dict[str, list[AIMessage]]:
        run_context = invocation_context or _invocation_context_from_messages(state["messages"])
        logger.info(
            "Agent IQ Pydantic run context: publication_context=%s publisher_configured=%s",
            run_context is not None,
            artifact_publisher is not None,
        )
        if run_context is not None:
            if artifact_publisher is None:
                raise ValueError(
                    "This invocation supplied artifact publication context, but publication "
                    "is unavailable."
                )
            agent, dependencies = create_pydantic_agent(
                agent_id,
                model=model,
                tools=tools,
                assets=assets,
                artifact_publisher=artifact_publisher,
                invocation_context=run_context,
            )
        else:
            agent, dependencies = base_agent, base_dependencies
        request, history = _request_from_messages(state["messages"])
        result = await agent.run(
            request.prompt,
            message_history=history,
            deps=dependencies,
        )
        tool_texts = [
            str(part.content)
            for message in result.all_messages()
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        answer = preserve_vacation_balance_payload(result.output.answer, tool_texts)
        answer = preserve_vpn_recovery_payload(answer, tool_texts)
        answer = append_missing_source_citations(
            answer,
            source_title_citations(tool_texts),
        )
        answer = append_missing_source_references(
            answer,
            source_reference_markers(tool_texts),
        )
        return {"messages": [AIMessage(content=answer)]}

    graph = StateGraph(MessagesState)
    graph.add_node("pydantic_ai", invoke)
    graph.add_edge(START, "pydantic_ai")
    graph.add_edge("pydantic_ai", END)
    return graph.compile()


class PublicationAwareResponsesHostServer(ResponsesHostServer):  # type: ignore[misc]
    """Strip machine context before LangGraph while preserving trusted run state."""

    async def build_input(
        self,
        request: Any,
        context: Any,
        *,
        skip_call_ids: frozenset[str] | None = None,
    ) -> dict[str, Any]:
        graph_input = cast(
            dict[str, Any],
            await super().build_input(
                request,
                context,
                skip_call_ids=skip_call_ids,
            ),
        )
        graph_input, publication_context_present = _strip_publication_context(graph_input)
        metadata_context = _publication_context_from_request(request)
        if metadata_context is not None:
            if publication_context_present:
                raise ValueError("Multiple artifact publication context modes are forbidden.")
            graph_input = _bind_publication_context(graph_input, metadata_context)
            publication_context_present = True
        logger.info(
            "Agent IQ hosted request context: publication_context=%s "
            "publication_transport=%s metadata_keys=%s",
            publication_context_present,
            "a2a_context_id" if metadata_context is not None else "prompt_or_none",
            _request_metadata_keys(request),
        )
        return graph_input


def _strip_publication_context(
    graph_input: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Remove machine prefixes and bind only the current turn's trusted context."""

    raw_messages = graph_input.get("messages")
    if not isinstance(raw_messages, list):
        raise ValueError("Responses host did not produce a messages-state input.")
    human_indices = [
        index for index, message in enumerate(raw_messages) if isinstance(message, HumanMessage)
    ]
    latest_human = human_indices[-1] if human_indices else None
    messages: list[BaseMessage] = []
    publication_context_present = False
    marker_messages = 0
    parsed_context_messages = 0
    parsed_ticket_messages = 0
    latest_human_length = 0
    for index, message in enumerate(raw_messages):
        if not isinstance(message, HumanMessage):
            messages.append(message)
            continue
        text = _message_text(message)
        if publication_context_marker_present(text) or publication_ticket_marker_present(text):
            marker_messages += 1
        if index == latest_human:
            latest_human_length = len(text)
        clean, coordinates = extract_publication_context(text)
        clean, ticket = extract_publication_ticket(clean)
        if coordinates is not None and ticket is not None:
            raise ValueError("Multiple artifact publication context modes are forbidden.")
        additional_kwargs = dict(message.additional_kwargs)
        additional_kwargs.pop(PUBLICATION_CONTEXT_MESSAGE_KEY, None)
        additional_kwargs.pop(PUBLICATION_TICKET_MESSAGE_KEY, None)
        if coordinates is not None:
            parsed_context_messages += 1
            if index == latest_human:
                additional_kwargs[PUBLICATION_CONTEXT_MESSAGE_KEY] = {
                    "invocationId": coordinates.invocation_id,
                    "correlationId": coordinates.correlation_id,
                }
                publication_context_present = True
        if ticket is not None:
            parsed_ticket_messages += 1
            if index == latest_human:
                additional_kwargs[PUBLICATION_TICKET_MESSAGE_KEY] = ticket.get_secret_value()
                publication_context_present = True
        messages.append(
            message.model_copy(
                update={
                    "content": clean,
                    "additional_kwargs": additional_kwargs,
                }
            )
        )
    graph_input["messages"] = messages
    logger.info(
        "Agent IQ hosted publication context probe: human_messages=%d "
        "marker_messages=%d parsed_context_messages=%d parsed_ticket_messages=%d "
        "latest_human_length=%d context_on_latest=%s",
        len(human_indices),
        marker_messages,
        parsed_context_messages,
        parsed_ticket_messages,
        latest_human_length,
        publication_context_present,
    )
    return graph_input, publication_context_present


def _publication_context_from_request(request: Any) -> ArtifactPublicationContext | None:
    metadata = _request_metadata(request)
    return decode_publication_context_id(metadata.get("a2a_context_id"))


def _bind_publication_context(
    graph_input: dict[str, Any],
    context: ArtifactPublicationContext,
) -> dict[str, Any]:
    raw_messages = graph_input.get("messages")
    if not isinstance(raw_messages, list):
        raise ValueError("Responses host did not produce a messages-state input.")
    latest_human = next(
        (
            index
            for index in range(len(raw_messages) - 1, -1, -1)
            if isinstance(raw_messages[index], HumanMessage)
        ),
        None,
    )
    if latest_human is None:
        raise ValueError("Artifact publication context requires a user message.")
    message = raw_messages[latest_human]
    additional_kwargs = dict(message.additional_kwargs)
    if (
        PUBLICATION_CONTEXT_MESSAGE_KEY in additional_kwargs
        or PUBLICATION_TICKET_MESSAGE_KEY in additional_kwargs
    ):
        raise ValueError("Hosted artifact publication context is ambiguous.")
    additional_kwargs[PUBLICATION_CONTEXT_MESSAGE_KEY] = {
        "invocationId": context.invocation_id,
        "correlationId": context.correlation_id,
    }
    messages = list(raw_messages)
    messages[latest_human] = message.model_copy(update={"additional_kwargs": additional_kwargs})
    graph_input["messages"] = messages
    return graph_input


def build_pydantic_hosted_graph(
    agent_id: str,
    settings: Settings | None = None,
) -> Any:
    """Load governed backends and compile one hosted PydanticAI graph."""

    active_settings = settings or get_settings()
    assets = load_consumer_runtime_assets()
    definition = definition_for(agent_id, assets)
    _require_pydantic_definition(definition)
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
    artifact_channel = active_settings.artifact_channel()
    publisher = (
        ManagedIdentityArtifactPublisher(
            artifact_channel.publish_endpoint,
            artifact_channel.application_id,
            artifact_channel.tenant_id,
            token_scope=artifact_channel.token_scope,
        )
        if artifact_channel is not None
        else None
    )
    return create_pydantic_graph(
        agent_id,
        model=pydantic_model(active_settings),
        tools=tools,
        assets=assets,
        artifact_publisher=publisher,
    )


def build_pydantic_hosted_server(
    agent_id: str,
    settings: Settings | None = None,
) -> ResponsesHostServer:
    """Create the Responses host with machine-prefix stripping enabled."""

    return PublicationAwareResponsesHostServer(
        build_pydantic_hosted_graph(agent_id, settings),
    )


def _request_from_messages(
    messages: Sequence[BaseMessage],
) -> tuple[PydanticHeroRequest, tuple[ModelMessage, ...]]:
    current_index = next(
        (
            index
            for index in range(len(messages) - 1, -1, -1)
            if isinstance(messages[index], HumanMessage)
        ),
        None,
    )
    if current_index is None:
        raise ValueError("A PydanticAI hosted invocation requires a user message.")
    if current_index != len(messages) - 1:
        raise ValueError("A PydanticAI hosted invocation must end with a user message.")
    request = PydanticHeroRequest(prompt=_message_text(messages[current_index]))
    history: list[ModelMessage] = []
    for message in messages[:current_index]:
        text = _message_text(message)
        if isinstance(message, HumanMessage):
            history.append(ModelRequest(parts=[UserPromptPart(content=text)]))
        elif isinstance(message, AIMessage):
            history.append(ModelResponse(parts=[TextPart(content=text)]))
        elif isinstance(message, SystemMessage):
            history.append(ModelRequest(parts=[SystemPromptPart(content=text)]))
        else:
            raise ValueError(
                f"Unsupported hosted conversation message type {message.__class__.__name__!r}."
            )
    return request, tuple(history)


def _message_text(message: BaseMessage) -> str:
    text = message.text.strip()
    if not text:
        raise ValueError("Hosted conversation messages cannot be empty.")
    return text


def _invocation_context_from_messages(
    messages: Sequence[BaseMessage],
) -> PydanticInvocationContext | None:
    latest_human = next(
        (message for message in reversed(messages) if isinstance(message, HumanMessage)),
        None,
    )
    if latest_human is None:
        return None
    raw_context = latest_human.additional_kwargs.get(PUBLICATION_CONTEXT_MESSAGE_KEY)
    raw_ticket = latest_human.additional_kwargs.get(PUBLICATION_TICKET_MESSAGE_KEY)
    if raw_context is None and raw_ticket is None:
        return None
    if raw_context is not None and raw_ticket is not None:
        raise ValueError("Hosted artifact publication context is ambiguous.")
    if raw_context is not None:
        if not isinstance(raw_context, Mapping) or set(raw_context) != {
            "invocationId",
            "correlationId",
        }:
            raise ValueError("Hosted artifact publication context is malformed.")
        invocation_id = raw_context.get("invocationId")
        correlation_id = raw_context.get("correlationId")
        if not isinstance(invocation_id, str) or not isinstance(correlation_id, str):
            raise ValueError("Hosted artifact publication context is malformed.")
        return PydanticInvocationContext(
            invocation_id=invocation_id,
            correlation_id=correlation_id,
        )
    if not isinstance(raw_ticket, str):
        raise ValueError("Hosted artifact publication context is malformed.")
    return PydanticInvocationContext(publication_ticket=SecretStr(raw_ticket))


def _request_metadata(request: Any) -> Mapping[str, Any]:
    metadata = getattr(request, "metadata", None)
    if isinstance(metadata, Mapping):
        return metadata
    as_dict = getattr(metadata, "as_dict", None)
    if callable(as_dict):
        payload = as_dict()
        if isinstance(payload, Mapping):
            return payload
    return {}


def _request_metadata_keys(request: Any) -> tuple[str, ...]:
    return tuple(sorted(str(key) for key in _request_metadata(request)))


def _artifact_tool_schema(
    allowed_kinds: Sequence[ArtifactKind],
) -> dict[str, Any]:
    definitions: dict[str, Any] = {}
    references: list[dict[str, str]] = []
    for kind in allowed_kinds:
        model = _ARTIFACT_MODELS[kind]
        schema = model.model_json_schema(by_alias=True)
        nested = schema.pop("$defs", {})
        for name, definition in nested.items():
            existing = definitions.get(name)
            if existing is not None and existing != definition:
                raise ValueError(f"Conflicting artifact schema definition {name!r}.")
            definitions[name] = definition
        definitions[model.__name__] = schema
        references.append({"$ref": f"#/$defs/{model.__name__}"})
    artifact_schema: dict[str, Any]
    if len(references) == 1:
        artifact_schema = references[0]
    else:
        artifact_schema = {
            "oneOf": references,
            "discriminator": {"propertyName": "kind"},
        }
    return {
        "type": "object",
        "properties": {"artifact": artifact_schema},
        "required": ["artifact"],
        "additionalProperties": False,
        "$defs": definitions,
    }


def _require_pydantic_definition(definition: HostedConsumerDefinition) -> None:
    if definition.descriptor.implementation.framework != PYDANTIC_AI_FRAMEWORK:
        raise ValueError(f"{definition.descriptor.agent_id!r} is not configured for PydanticAI.")


def _openai_v1_base_url(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("The PydanticAI model endpoint must be an absolute HTTPS URL.")
    path = parsed.path.rstrip("/")
    if not path.endswith("/openai/v1"):
        path = "/openai/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, f"{path}/", "", ""))


def _foundry_project_v1_base_url(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("The Foundry project endpoint must be an absolute HTTPS URL.")
    path = parsed.path.rstrip("/")
    if not path:
        raise ValueError("The Foundry project endpoint must include its project path.")
    if not path.endswith("/openai/v1"):
        path = f"{path}/openai/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, f"{path}/", "", ""))
