# Contributing to Agent IQ

## Development workflow

1. Create a focused branch and keep each change aligned to one product
   milestone.
2. Add or update tests before changing cloud state.
3. Run `pwsh .\scripts\verify.ps1`.
4. Use the smallest live probe needed after deterministic gates pass.
5. Record deployment and rollback identifiers for every cloud mutation.

## Engineering rules

- Keep `import agent_iq` side-effect free.
- Put framework- and vendor-specific behavior behind adapters.
- Preserve type safety and explicit error propagation.
- Filter permission and trust before retrieval, then re-check before invocation.
- Expose scores and public card matches, never private chain-of-thought.
- Never turn an error, missing source, or blocked action into a successful result.
- Keep public names under the Agent IQ capability hierarchy.
- Do not add generated deployment folders, caches, or vendored environments.
- Treat `reports\` as ignored, runtime-generated evidence. Never commit or
  publish report JSON/HTML, latest/history pointers, locks, or certificates.
- Do not recreate live PASS evidence locally. Generate a fresh consumer-only
  certificate only after the corresponding deployment or recreation is approved.

## Credentials and cloud access

- Prefer managed identity and `DefaultAzureCredential`.
- Use process environment only when a service requires a key.
- Never commit or print credentials, token payloads, tenant-specific generated
  state, or connection strings.
- Cloud scripts must support a non-mutating validation mode and explicit target
  selection.
- Production promotion always requires an explicit operator action.

## Source provenance

Any behavior or data ported from another repository must be recorded in
`docs\provenance\sources.json` with its source path, license, revision, files,
and the Agent IQ destination. Generated output is not a source.
