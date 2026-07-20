"""Typed operational exceptions backed by stable Agent IQ error contracts."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from agent_iq.contracts import AgentIqError, ErrorCode, ErrorDetail


class AgentIqOperationError(RuntimeError):
    """An operational failure safe to translate across API boundaries."""

    def __init__(self, error: AgentIqError) -> None:
        super().__init__(error.message)
        self.error = error


def operation_error(
    code: ErrorCode,
    message: str,
    *,
    retryable: bool = False,
    retry_after_seconds: int | None = None,
    details: Sequence[ErrorDetail] = (),
    correlation_id: str | None = None,
) -> AgentIqOperationError:
    """Build an exception with a unique, language-neutral error payload."""

    return AgentIqOperationError(
        AgentIqError(
            error_id=f"err-{uuid.uuid4().hex}",
            code=code,
            message=message,
            retryable=retryable,
            retry_after_seconds=retry_after_seconds,
            details=tuple(details),
            correlation_id=correlation_id,
        )
    )
