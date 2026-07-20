# Match

Match consumes canonical agent descriptors and a caller-scoped request:

1. apply eligibility policy before any retrieval;
2. flatten public name, description, skill, tag, and example fields;
3. retrieve with BM25 and either deterministic offline semantics or real Azure
   embeddings;
4. fuse ranks with reciprocal-rank fusion;
5. select one primary agent and additional agents only for distinct task
   clauses;
6. return component scores, matched public fields, policy facts, and selected
   work items.

The implementation never uses target-agent framework metadata for ranking.
`framework: "LangGraph"` describes the initial synthetic consumer runtime but
does not affect Match.

## Semantic modes

- `offline` always uses deterministic token plus character n-gram TF-IDF.
- `azure` requires an Azure endpoint and embedding deployment and fails
  explicitly if configuration, identity, or the service fails.
- `auto` chooses Azure only when complete Azure coordinates are present;
  otherwise it intentionally chooses offline.

An Azure error never silently becomes an offline success.

## Fusion tie handling

RRF combines lexical and semantic ranks. If reciprocal scores are exactly tied,
Match uses stronger lexical score, then semantic score, then canonical agent ID.
This prevents arbitrary ID ordering from defeating strong domain-token evidence
while retaining deterministic output.

## Quality gate

```powershell
. .\scripts\dev_env.ps1
uv run python .\scripts\run_discovery_eval.py --semantic offline --json
uv run python .\scripts\run_discovery_eval.py --semantic azure --json
```

The 33-case, 20-agent synthetic consumer gate requires recall@k, MRR, and exact
selection 1.000, with zero false positives. Offline p95 must remain below 250 ms
on the development machine; live latency is measured and reported rather than
hidden behind a fallback.
