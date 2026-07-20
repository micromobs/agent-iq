# Start here

Choose the path that matches your goal:

| Goal | Start with |
| --- | --- |
| See Agent IQ locally | `pwsh .\scripts\agent-iq.ps1 demo` |
| Run the quality suite | `pwsh .\scripts\agent-iq.ps1 test` |
| Deploy to Azure | [`docs/DEPLOY_TO_AZURE.md`](docs/DEPLOY_TO_AZURE.md) |
| Install in Cowork | [`docs/INSTALL_IN_COWORK.md`](docs/INSTALL_IN_COWORK.md) |
| Try supported prompts | [`docs/SCENARIOS.md`](docs/SCENARIOS.md) |
| Understand the system | [`docs/architecture/overview.md`](docs/architecture/overview.md) |
| Contribute code | [`AGENTS.md`](AGENTS.md) and [`CONTRIBUTING.md`](CONTRIBUTING.md) |

Agent IQ's core rule is simple: user intent and request-time trust dynamically
form the smallest justified squad. Atomic skills call generic orchestration;
they never hard-code which agents participate.

The local showcase and offline evaluations need no credentials. Azure deployment
is an explicit, billable operation with a dry-run plan, generated state outside
Git, live verification, and complete teardown.
