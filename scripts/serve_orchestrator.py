"""Run the isolated Agent IQ MCP + HTTP/SSE + A2A canary host."""

from __future__ import annotations

import argparse
import logging

import uvicorn

from agent_iq.api.host import build_live_resources, create_product_app
from agent_iq.config import Settings
from agent_iq.telemetry import configure_agent_iq_telemetry


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    arguments = parser.parse_args()

    settings = Settings()
    if settings.public_base_url is None:
        raise ValueError("AGENT_IQ_PUBLIC_BASE_URL is required.")
    public_base_url = str(settings.public_base_url).rstrip("/")
    deployment_probe_base_url = (
        str(settings.deployment_probe_base_url).rstrip("/")
        if settings.deployment_probe_base_url is not None
        else None
    )
    logging.basicConfig(level=settings.log_level)
    configure_agent_iq_telemetry()
    resources = build_live_resources(settings, public_base_url=public_base_url)
    app = create_product_app(
        resources,
        public_base_url=public_base_url,
        deployment_probe_base_url=deployment_probe_base_url,
    )
    uvicorn.run(
        app,
        host=arguments.host,
        port=arguments.port,
        log_level=settings.log_level.casefold(),
    )


if __name__ == "__main__":
    main()
