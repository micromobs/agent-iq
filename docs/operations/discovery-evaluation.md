# Discovery evaluation

The same 33 labelled synthetic consumer intents gate both deterministic and
real-model semantics. These are transparent certification fixtures, not private
brand data. A run passes only when all of these are true:

- recall@k = 1.000;
- mean reciprocal rank = 1.000;
- exact selected-agent set = 1.000;
- false positives = 0.

## Verified baseline

| Date | Semantic backend | Agents | Cases | Recall | MRR | Exact | False positives | p95 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 2026-07-10 | Offline token + character n-gram TF-IDF | 20 | 33 | 1.000 | 1.000 | 1.000 | 0 | 10-27 ms |
| 2026-07-10 | Foundry `text-embedding-3-large-gs` | 20 | 33 | 1.000 | 1.000 | 1.000 | 0 | 2.61 s |

The Azure client reports `semanticBackend: "azure-embeddings"` in the result.
It never reports an Azure run while silently using offline semantics.

## Run offline

```powershell
. .\scripts\dev_env.ps1
uv run python .\scripts\run_discovery_eval.py --semantic offline --json
```

The run persists a human-readable scorecard at
`reports\evals\discovery\latest.html`. The same directory contains `latest.json`,
`history.json`, and immutable JSON/HTML files under `runs\`. To run the gate and
open the scorecard:

```powershell
pwsh .\scripts\run_offline_evals.ps1 -Open
```

Reports include every labelled case, selected and expected agents, top-ranked
candidates, latency, semantic-backend provenance, and each release gate. The
generated report directory is intentionally ignored by Git.

## Run with Foundry embeddings

Authenticate with the approved Entra identity, then set the Azure OpenAI
endpoint and embedding deployment in the same process:

```powershell
$env:AGENT_IQ_AZURE_OPENAI_ENDPOINT = $env:AZURE_OPENAI_ENDPOINT
$env:AGENT_IQ_AZURE_EMBEDDING_DEPLOYMENT = $env:AZURE_EMBEDDING_DEPLOYMENT
uv run python .\scripts\run_discovery_eval.py --semantic azure --json
```

Do not set or persist an API key when managed identity or Entra authentication
is available.
