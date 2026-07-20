# Product contracts

Agent IQ publishes strict immutable Python models and equivalent Draft 2020-12
JSON Schemas for:

- canonical agents and capabilities;
- match requests and selection decisions;
- execution plans;
- lifecycle event envelopes;
- structured evidence and citations;
- durable tasks;
- stable errors.

Wire fields use camelCase and reject unknown fields. Top-level contracts carry
`schemaVersion: "1.0"`. Run:

```powershell
. .\scripts\dev_env.ps1
uv run python .\scripts\generate_schemas.py
uv run python .\scripts\generate_schemas.py --check
```

Pydantic is the validation implementation for these product contracts and
configuration only. It does not select or constrain the target agent framework.
For example, the initial synthetic consumer descriptors record `framework:
"LangGraph"` as informational implementation metadata, while Match and
Orchestrate use only canonical capabilities, eligibility, skills, and evidence.

Selection explanations contain public card matches, component scores, and
policy facts. No contract has a private-reasoning or chain-of-thought field.
