# Demo runbook

## Credential-free demo

```powershell
pwsh .\scripts\agent-iq.ps1 demo
```

Show:

1. one intent entering the catalog;
2. the smallest eligible squad forming dynamically;
3. compact execution progress;
4. source-bound evidence and citations; and
5. an honest completed, partial, or declined result.

Use the VPN and Vacation flows for compact single-agent UX. Use the mixed VPN
and Expense prompt to show dynamic multi-agent formation without a composite
skill.

## Live Cowork demo

```powershell
pwsh .\scripts\agent-iq.ps1 status
pwsh .\scripts\agent-iq.ps1 test
```

Only proceed when both commands pass. Install the generated package as described
in [Install in Cowork](INSTALL_IN_COWORK.md), start a fresh conversation, and
use the exact prompts in [Scenarios](SCENARIOS.md).

State clearly that:

- Cowork calls Agent IQ through MCP;
- Agent IQ invokes selected Foundry agents through native A2A;
- labelled fixtures are not live enterprise systems; and
- the developer connector is anonymous and not production-ready.

Do not narrate raw transport events, internal file names, hidden model
reasoning, or unsupported autonomy.
