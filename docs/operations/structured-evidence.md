# Structured evidence

Agent IQ uses native Microsoft Foundry A2A for agent answers and an
authenticated companion channel for machine-verifiable evidence records.

## Decision

Microsoft Foundry incoming A2A is currently a preview, requires the Responses
protocol, and supports text modality only. A live `consumer-formulation-rd` call
also returns a completed A2A task containing `TextPart` output and no `DataPart`.
Agent IQ therefore does not infer source identity from model text or depend on
an undocumented Responses-to-A2A structured-output conversion.

The selected transport is:

1. Invoke the agent through native authenticated A2A.
2. Retrieve structured references from the same canonical Foundry IQ knowledge
   binding.
3. Allowlist references against packaged corpus documents.
4. Publish an opaque, expiring evidence handle correlated to the A2A invocation.
5. Require the same tenant, caller, invocation, and correlation identifiers to
   fetch the evidence envelope.

Unknown sources fail closed with `evidence_unavailable`. Identity or correlation
mismatches fail with `authorization_denied`.

## Multi-agent normalization

Each agent invocation has its own local reference namespace, so two agents may
both emit `[ref_id:0]`. Agent IQ rewrites only markers that resolve to structured
records from that invocation. It then:

1. de-duplicates sources by canonical document key, URI, or source ID plus
   knowledge binding - never by display title;
2. rejects a stable source identity that returns conflicting content hashes;
3. assigns one global, one-based citation number in first-use order;
4. preserves unknown markers as unresolved instead of fabricating links;
5. serves citation details from an identity-bound execution URL;
6. omits successful agent prose that has no structured citation and reports the
   omission as a partial result.

The deterministic synthesizer reports complete, partial, or declined status,
claim-level agent/source provenance, conflicts, grounding coverage, and explicit
failed, timed-out, cancelled, or declined participants. It never treats arbitrary
model-formatted prose as evidence metadata.

Validated PydanticAI and SAP-facing business objects use the same companion
pattern but a separate typed-artifact contract. See
[`typed-artifacts.md`](typed-artifacts.md).

## Agent Card boundary

The Agent IQ catalog descriptor advertises
`urn:agent-iq:capability:structured-evidence:v1` with companion delivery.
Foundry's writable Agent Card subset contains description, version, and skills;
custom `capabilities.extensions` are not sent to Foundry. Live reconciliation
records this platform omission without weakening drift checks for supported
fields.

## Certification

From the repository root:

```powershell
pwsh .\scripts\certify_foundry_evidence.ps1
```

The command does not redeploy the hosted agent. It verifies:

- the standard native A2A answer path;
- a text-only A2A event shape;
- at least two stable Foundry IQ source records;
- required BrightWash and comparison sources;
- unknown-source rejection;
- caller, tenant, invocation, and correlation isolation;
- correlated companion response metadata;
- content-safe reporting under `reports\evals\evidence\latest.json`.

## References

- Incoming A2A limitations and protocol setup:
  <https://github.com/MicrosoftDocs/azure-ai-docs/blob/04c093649faec3beea023e8407d5af4c8119f7be/articles/foundry/agents/how-to/enable-agent-to-agent-endpoint.md>
- Azure SDK structured Responses sample, which requires output items before the
  terminal completion event:
  <https://github.com/Azure/azure-sdk-for-python/blob/0ad3d051582af72c291cbaf50f2efbdae95fdadb/sdk/agentserver/azure-ai-agentserver-responses/samples/sample_16_structured_outputs.py>

The SDK sample explains why the removed experimental probe was invalid: it
attempted to splice a structured item around an already-created terminal event.
Certification now relies on the documented platform boundary plus the live
standard A2A shape instead of repeated temporary deployments.
