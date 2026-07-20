"""Generic entry point for the reusable Agent IQ LangGraph host image."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping, Sequence
from typing import Any

from agent_iq.config import Settings

from .host_descriptor import RuntimeHostDescriptor, descriptor_from_environment

DEFAULT_PORT = 8088


def check_host_environment(
    environment: Mapping[str, str],
) -> RuntimeHostDescriptor:
    """Validate image selection without credentials, network access, or model calls."""

    return descriptor_from_environment(environment)


def build_server_from_environment() -> tuple[RuntimeHostDescriptor, Any]:
    """Build the selected hosted server after validating immutable inputs."""

    from .hosted_consumer import build_hosted_server

    descriptor = check_host_environment(os.environ)
    settings = Settings(_env_file=None)
    if settings.toolbox_name != descriptor.toolbox_name:
        raise RuntimeError("Validated toolbox selection drifted while loading host settings.")
    return descriptor, build_hosted_server(descriptor.logical_agent_id, settings)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)
    if args.check:
        descriptor = check_host_environment(os.environ)
        print(json.dumps(descriptor.model_dump(mode="json", by_alias=True), sort_keys=True))
        return 0

    _, server = build_server_from_environment()
    port = args.port if args.port is not None else int(os.environ.get("PORT", DEFAULT_PORT))
    server.run(port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
