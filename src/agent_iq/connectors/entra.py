"""Async bearer-token abstraction for keyless Azure connectors."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Protocol, cast

from azure.core.pipeline.policies import RedirectPolicy
from azure.core.pipeline.transport import AioHttpTransport, RequestsTransport
from azure.identity import DefaultAzureCredential
from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential


class AccessTokenLike(Protocol):
    token: str


class SyncTokenCredential(Protocol):
    def get_token(self, *scopes: str, **kwargs: Any) -> AccessTokenLike: ...

    def close(self) -> None: ...


class BearerTokenProvider(Protocol):
    async def get_token(self, scope: str) -> str: ...

    async def close(self) -> None: ...


def isolated_default_azure_credential(
    *,
    exclude_interactive_browser_credential: bool = False,
    credential_factory: Callable[..., DefaultAzureCredential] | None = None,
) -> DefaultAzureCredential:
    """Create a credential that cannot inherit proxies or follow redirects."""

    factory = credential_factory or DefaultAzureCredential
    options: dict[str, Any] = {
        "transport": RequestsTransport(use_env_settings=False),
        "redirect_policy": RedirectPolicy(permit_redirects=False),
    }
    if exclude_interactive_browser_credential:
        options["exclude_interactive_browser_credential"] = True
    return factory(**options)


def isolated_async_default_azure_credential(
    *,
    credential_factory: Callable[..., AsyncDefaultAzureCredential] | None = None,
) -> AsyncDefaultAzureCredential:
    """Create an async credential isolated from proxies and redirects."""

    factory = credential_factory or AsyncDefaultAzureCredential
    return factory(
        transport=AioHttpTransport(use_env_settings=False),
        redirect_policy=RedirectPolicy(permit_redirects=False),
    )


class DefaultAzureBearerTokenProvider:
    """Adapt DefaultAzureCredential without blocking the async runtime."""

    def __init__(self, credential: SyncTokenCredential | None = None) -> None:
        self._credential = credential or cast(
            SyncTokenCredential,
            isolated_default_azure_credential(
                credential_factory=DefaultAzureCredential,
            ),
        )
        self._owns_credential = credential is None

    async def get_token(self, scope: str) -> str:
        access_token = await asyncio.to_thread(self._credential.get_token, scope)
        return access_token.token

    async def close(self) -> None:
        if self._owns_credential:
            await asyncio.to_thread(self._credential.close)
