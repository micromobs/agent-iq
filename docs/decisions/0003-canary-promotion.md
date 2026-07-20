# ADR 0003: Parallel canary and explicit promotion

**Status:** Accepted

## Decision

Build the first product path in a separate canary Container App and Cowork
package. Use the existing Foundry project with versioned agent rollout, but do
not repoint the working connector until a release report passes and promotion is
explicitly approved.

## Consequences

- The current demo remains available during development.
- Agent, app, catalog, and package rollback are independent.
- Cloud scripts require explicit target selection and produce deployment
  records.
