# ADR 0004: Structured evidence is the citation authority

**Status:** Accepted

## Decision

Treat transport-preserved structured evidence as the only authority for
citations. Inline model markers are presentation hints and can be rewritten
only when they map to a structured source record.

If Foundry A2A cannot preserve data parts or artifact metadata, use an
authenticated invocation-bound evidence endpoint rather than parsing arbitrary
answer prose.

## Consequences

- Cross-agent citation IDs can be normalized safely.
- Missing or unknown references stay unresolved rather than becoming fabricated
  links.
- Evidence access must enforce caller and tenant boundaries.
