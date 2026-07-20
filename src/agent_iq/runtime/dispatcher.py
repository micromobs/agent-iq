"""Runtime dispatch boundary for native-only Demo 1 execution."""

from __future__ import annotations

import asyncio
from typing import Protocol

from .models import NativeA2ARequest, NativeA2AResult


class NativeA2AInvoker(Protocol):
    async def invoke(
        self,
        request: NativeA2ARequest,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> NativeA2AResult: ...


class NativeOnlyDispatcher:
    """Dispatch every selected agent through the one allowed native A2A runtime."""

    def __init__(self, runtime: NativeA2AInvoker) -> None:
        self._runtime = runtime

    async def dispatch(
        self,
        request: NativeA2ARequest,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> NativeA2AResult:
        return await self._runtime.invoke(request, cancel_event=cancel_event)
