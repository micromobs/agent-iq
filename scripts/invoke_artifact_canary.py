"""Invoke one hosted agent through native A2A with non-secret publication coordinates."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from agent_iq.runtime import NativeA2ARequest
from agent_iq.runtime.adapters import NativeA2ARuntime


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-endpoint", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--invocation-id", required=True)
    parser.add_argument("--correlation-id", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


async def _invoke(args: argparse.Namespace) -> dict[str, object]:
    request = NativeA2ARequest(
        invocation_id=args.invocation_id,
        correlation_id=args.correlation_id,
        agent_id=args.agent_id,
        prompt=args.prompt,
        deadline_seconds=300,
        enable_artifact_publication=True,
    )
    async with NativeA2ARuntime(args.project_endpoint) as runtime:
        result = await runtime.invoke(request)
    return {
        "schemaVersion": "1.0",
        "status": "pass",
        "agentId": result.agent_id,
        "agentVersion": result.agent_version,
        "invocationId": result.invocation_id,
        "correlationId": result.correlation_id,
        "protocolVersion": result.protocol_version,
        "durationMs": result.duration_ms,
        "answerHash": f"sha256:{hashlib.sha256(result.answer.encode()).hexdigest()}",
        "answerNonempty": bool(result.answer.strip()),
        "publicationTicketPersisted": False,
    }


def main() -> None:
    args = _parse_args()
    payload = asyncio.run(_invoke(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
        encoding="ascii",
    )
    temporary.replace(args.output)
    print(
        f"Native A2A artifact canary: PASS agent={payload['agentId']} "
        f"version={payload['agentVersion']}"
    )


if __name__ == "__main__":
    main()
