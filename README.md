# Netgen SDK release automation

This standalone repository builds, qualifies, and publishes the FEATool Netgen
native SDKs. It does not vendor an editable checkout of the complete Netgen source
tree.

## Downloads / Releases

Published SDKs are available from the repository's GitHub Releases page:
https://github.com/precise-simulation/netgen-sdk/releases

Each release contains eight qualified native SDK archives covering Windows,
Linux, and macOS shared/static variants, with an adjacent SHA-256 checksum for
every SDK archive. Archive filenames intentionally retain the established
`netgen-featool-v<version>-...` prefix for consumer compatibility.

Releases also include a deterministic archive of the exact reconstructed patched
Netgen source used by the producer, plus its SHA-256 checksum. The source archive
embeds `source-provenance.json`, recording the pinned upstream repository/tag/SHA
and ordered FEATool patch SHA-256 values. GitHub's automatically generated source
archives describe the automation tag; use the explicitly published patched-source
archive when exact build-source provenance is required.

The source built by GitHub Actions is reconstructed from two repository-owned
inputs:

- `upstream/netgen.json` pins the canonical NGSolve Netgen repository, release
  tag, and exact full commit SHA.
- `patches/netgen/series` lists the FEATool patch files in the exact order in
  which they are validated and applied.

`scripts/prepare-netgen-source.py` shallow-fetches the configured upstream tag,
verifies that it resolves to the pinned commit, applies the canonical LF patch
series, checks the resulting diff, and writes `source-provenance.json`.
`scripts/package-netgen-source.py` packages that exact reconstructed tree for
release. Windows, Linux, and macOS producers all build from the same source
identity and record the upstream identity plus ordered patch SHA-256 values in
each SDK archive.

The active tree primarily contains:

- `.github/` producer, qualification, OCCT-update, and release automation;
- `patches/netgen/` and `upstream/netgen.json` source-reconstruction inputs;
- `scripts/` source, provenance, and release validation helpers;
- `tests/windows-native-sdk/` and `tests/unix-native-sdk/` external SDK consumer
  fixtures;
- `plans/` repository planning instructions and future execution records;
- `AUTHORS` and `LICENSE` attribution/licensing material.

All Netgen compilation and runtime qualification is performed by GitHub Actions.
The release process and upstream-port procedure are documented in
`.github/README.md`.

This standalone producer starts with fresh Git history. Its initial producer tree
was migrated from `precise-simulation/netgen` revision
`2b322922e9cf2a3b34014b448cf83d2dd2045e5f`. Historical source-fork commits, tags,
and releases remain in that repository; new Netgen SDK automation and releases
live in `precise-simulation/netgen-sdk`.
