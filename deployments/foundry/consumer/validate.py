"""CLI wrapper for consumer Foundry hosted-agent validation."""

from agent_iq.deployments.foundry_consumer import cli_validate

if __name__ == "__main__":
    raise SystemExit(cli_validate())
