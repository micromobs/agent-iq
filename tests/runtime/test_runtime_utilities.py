"""Runtime utility, dispatcher, model, and Entra adapter tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

import agent_iq.connectors.entra as entra
import agent_iq.runtime.adapters.native_a2a as native_a2a
from agent_iq.connectors.entra import DefaultAzureBearerTokenProvider
from agent_iq.runtime import IdempotencyPolicy, NativeA2ARequest, NativeOnlyDispatcher
from agent_iq.runtime.adapters import event_text_from_sdk
from agent_iq.runtime.models import NativeA2AResult


class FakeCredential:
    def __init__(self) -> None:
        self.closed = False

    def get_token(self, *scopes: str, **kwargs: Any) -> Any:
        del scopes, kwargs
        return SimpleNamespace(token="token")

    def close(self) -> None:
        self.closed = True


def test_entra_provider_adapts_owned_and_injected_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    injected = FakeCredential()
    provider = DefaultAzureBearerTokenProvider(injected)
    assert asyncio.run(provider.get_token("scope")) == "token"
    asyncio.run(provider.close())
    assert injected.closed is False

    owned = FakeCredential()
    credential_options: dict[str, Any] = {}

    def credential_factory(**kwargs: Any) -> FakeCredential:
        credential_options.update(kwargs)
        return owned

    monkeypatch.setattr(entra, "DefaultAzureCredential", credential_factory)
    owned_provider = DefaultAzureBearerTokenProvider()
    asyncio.run(owned_provider.close())
    assert owned.closed is True
    assert credential_options["transport"]._use_env_settings is False
    assert credential_options["redirect_policy"].allow is False


def test_native_only_dispatcher_delegates_cancellation() -> None:
    cancel_event = asyncio.Event()

    class FakeInvoker:
        async def invoke(
            self,
            request: NativeA2ARequest,
            *,
            cancel_event: asyncio.Event | None = None,
        ) -> NativeA2AResult:
            assert cancel_event is not None
            return NativeA2AResult(
                invocation_id="inv-test",
                agent_id=request.agent_id,
                agent_version="1",
                protocol_version="1.0",
                message_id="msg-test",
                answer="answer",
                duration_ms=1,
                attempt_count=1,
            )

    async def run() -> NativeA2AResult:
        dispatcher = NativeOnlyDispatcher(FakeInvoker())
        return await dispatcher.dispatch(
            NativeA2ARequest(agent_id="consumer-formulation-rd", prompt="Prompt."),
            cancel_event=cancel_event,
        )

    assert asyncio.run(run()).agent_id == "consumer-formulation-rd"


def test_native_request_rejects_unsafe_retry_and_card_options() -> None:
    with pytest.raises(ValidationError, match="At least one"):
        NativeA2ARequest(
            agent_id="consumer-formulation-rd",
            prompt="Prompt.",
            preferred_card_versions=(),
        )
    with pytest.raises(ValidationError, match="Supported"):
        NativeA2ARequest(
            agent_id="consumer-formulation-rd",
            prompt="Prompt.",
            preferred_card_versions=("2.0",),
        )
    with pytest.raises(ValidationError, match="duplicates"):
        NativeA2ARequest(
            agent_id="consumer-formulation-rd",
            prompt="Prompt.",
            preferred_card_versions=("1.0", "1.0"),
        )
    with pytest.raises(ValidationError, match="requires an idempotency key"):
        NativeA2ARequest(
            agent_id="consumer-formulation-rd",
            prompt="Prompt.",
            idempotency_policy=IdempotencyPolicy.RETRY_WITH_KEY,
        )
    with pytest.raises(ValidationError, match="Multiple attempts"):
        NativeA2ARequest(
            agent_id="consumer-formulation-rd",
            prompt="Prompt.",
            max_attempts=2,
        )


def test_event_text_handles_every_official_sdk_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(native_a2a, "get_message_text", lambda value, delimiter: value)
    monkeypatch.setattr(native_a2a, "get_artifact_text", lambda value, delimiter: value)

    def event(which: str, **values: Any) -> Any:
        return SimpleNamespace(WhichOneof=lambda name: which, **values)

    assert event_text_from_sdk(event("message", message="message")) == "message"
    task = SimpleNamespace(
        artifacts=["one", "two"],
        status=SimpleNamespace(HasField=lambda name: False),
    )
    assert event_text_from_sdk(event("task", task=task)) == "one two"
    empty_task = SimpleNamespace(
        artifacts=[],
        status=SimpleNamespace(HasField=lambda name: True, message="status"),
    )
    assert event_text_from_sdk(event("task", task=empty_task)) == "status"
    artifact = SimpleNamespace(artifact="artifact")
    assert event_text_from_sdk(event("artifact_update", artifact_update=artifact)) == "artifact"
    status = SimpleNamespace(status=empty_task.status)
    assert event_text_from_sdk(event("status_update", status_update=status)) == "status"
    assert event_text_from_sdk(event("unknown")) == ""
