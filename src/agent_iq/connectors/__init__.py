"""Microsoft and external context connectors."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .entra import BearerTokenProvider, DefaultAzureBearerTokenProvider

__all__ = ["BearerTokenProvider", "DefaultAzureBearerTokenProvider"]


def __getattr__(name: str) -> Any:
    """Avoid loading orchestration-only identity code in hosted agents."""

    if name in {"BearerTokenProvider", "DefaultAzureBearerTokenProvider"}:
        from .entra import BearerTokenProvider, DefaultAzureBearerTokenProvider

        return {
            "BearerTokenProvider": BearerTokenProvider,
            "DefaultAzureBearerTokenProvider": DefaultAzureBearerTokenProvider,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
