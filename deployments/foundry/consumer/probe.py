"""CLI wrapper for strict native-A2A consumer canary certification."""

from agent_iq.deployments.foundry_canary import cli_probe

if __name__ == "__main__":
    raise SystemExit(cli_probe())
