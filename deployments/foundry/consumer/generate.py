"""CLI wrapper for consumer Foundry hosted-agent generation."""

from agent_iq.deployments.foundry_consumer import cli_generate

if __name__ == "__main__":
    raise SystemExit(cli_generate())
