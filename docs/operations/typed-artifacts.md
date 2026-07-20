# Invocation-bound typed business artifacts

Foundry incoming A2A currently preserves user-facing text but not the validated
structured output produced inside every hosted framework. Agent IQ therefore
keeps native A2A as the answer transport and publishes business artifacts
through an authenticated companion channel.

Supported versioned artifacts are:

- `DemandScenario`
- `SupplyCommitment`
- `RegulatoryDecision`
- `SapChangeProposal`

`SapChangeProposal.approvalRequired` is always `true`. Publishing a proposal
does not execute a SAP mutation.

## Security boundary

The coordinator authorizes an invocation before calling the agent. The grant
binds:

- tenant and caller;
- invocation and correlation IDs;
- expected producer agent, version, and authenticated identity;
- allowed artifact kinds;
- expiry.

The hosted `publish_typed_artifact` tool accepts only a discriminated typed
artifact. It never accepts invocation, correlation, caller, or producer claims
from model-visible arguments. The coordinator issues a short-lived opaque ticket
but retains it inside the companion store. Native A2A carries only invocation
and correlation IDs in the protocol-level `Message.contextId`, encoded as
`aiq-pub-v1|<invocation>|<correlation>`. Foundry exposes that value to the
custom Responses host as trusted `a2a_context_id` metadata. The host validates
it and binds the coordinates to private LangGraph message state; publication
coordinates and tickets never enter model-visible prompt text.

The hosted publisher obtains an Entra token with its managed identity and uses
the private coordinates to claim the existing ticket from
`/v1/artifacts/tickets/claim`. The claim endpoint validates JWT signature,
issuer, tenant, audience, expiry, object ID, invocation, correlation, grant
expiry, and revocation before returning the ticket with `Cache-Control:
no-store`. The publisher then sends only the bearer token, claimed opaque
ticket, and typed artifact to `/v1/artifacts/publish`.

The reusable canary binds the channel to one tenant and one application GUID.
The managed identity requests `<application-id>/.default`; the JWT audience is
the application GUID itself. Endpoint, application ID, and tenant ID must be
configured together, and the audience service principal must exist without
app-role-assignment enforcement.

For live orchestration, the producer version and managed-identity object ID are
resolved from Foundry immediately before the ticket is issued. Static authored
identity is never the trust source for a live publication grant.

Fetch enforces the opaque handle plus caller, tenant, invocation, correlation,
artifact ID, and TTL. A repeated identical publication is idempotent; reusing
an artifact ID with different content fails with `artifact_conflict`.

## Framework boundary

PydanticAI exposes the synthetic publication tool with a strict discriminated
business-artifact schema and validates the selected artifact type before the
managed-identity callback. Agent IQ stores that object unchanged behind an
opaque reference. The orchestrator never reconstructs `DemandScenario`,
`SupplyCommitment`, `RegulatoryDecision`, or `SapChangeProposal` from A2A answer
prose.

The four payload schemas and the envelope schema are published under
`schemas/v1/`.

## Live certification

Demand Forecasting is certified through native Foundry A2A with a
PydanticAI-authored `DemandScenario` published to an isolated Azure companion
host. The content-safe aggregate report is:

```text
reports/evals/artifact-live/latest.json
```

It requires native A2A v1.0, exact invocation/correlation/producer binding,
strict artifact validation, a pinned companion image, no persisted ticket, and
fail-closed claim checks for wrong identity, wrong invocation, wrong
correlation, and missing credentials. The certificate also requires publication
denials for wrong identity, wrong ticket, and missing ticket, plus expiration
and revocation coverage. No ticket is transported through the A2A prompt or
persisted in the certificate.

The current reusable canary uses the authenticated deployment application's
existing service principal as the token resource. This is safe because the
application is only an audience: authorization still requires the opaque ticket,
an exact JWT `oid` match to the live Foundry producer identity, the configured
tenant and audience, and an allowed artifact kind.

The companion store is process-local. The canary must therefore remain in
single-revision mode with one replica. Multi-revision traffic or horizontal
scale requires a durable shared broker first.
