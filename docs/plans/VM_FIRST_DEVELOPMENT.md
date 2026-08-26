# VM-first development amendment

Status: active development workflow from 2026-08-26.

This additive amendment separates exploratory format discovery from official
evidence acquisition. It supersedes the implementation plan's serial campaign
and per-step approval process only for ordinary development. Existing
preregistered plans and provenance history remain immutable, and the release
contracts in `docs/validation/` remain binding for support claims.

## Development loop

Work in dependency-sized vertical slices: opening, catalog, table definitions,
rows, values, indexes, then creation and updates. For each slice:

1. Run a small declarative scenario through licensed DAO in a local Windows VM.
2. Keep the live MDB on the guest's local disk; copy it only after DAO closes.
3. Retain the scenario, executed-source hashes, provider identity, operation
   log, semantic snapshot, and output hashes as private exploratory output.
4. Analyze the result and implement the smallest Rust behavior it establishes.
5. Add focused boundary and corruption tests.
6. Record concise provenance before merging production code that relies on a
   newly observed format fact.

Exploratory runs are intentionally repeatable. A failed run can be diagnosed
and rerun after changing the scenario or harness. Each output must identify its
actual inputs and declare `development_only = true`; no exploratory output can
advance verification state or claim compatibility.

## Repository and machine boundary

The repository contains portable, non-secret scenario logic, allowlisted
runners, validators, and tests. The machine-local VM directory contains:

- Windows disks and snapshots;
- licensed installers and provider binaries;
- credentials and SSH private keys;
- generated MDB files and raw run output; and
- container runtime state and logs.

Do not mount the repository as a writable Windows working copy. Exchange
bounded requests and completed artifacts through a dedicated shared directory.

## Validation cadence

- During iteration, run focused tests for the touched component.
- At a completed vertical slice, run the cross-platform Rust matrix and the
  applicable local DAO semantic differential.
- For a release candidate, freeze the scenario inventory and execute the full
  clean-commit acceptance and DAO evidence contracts.

Preregistration, holdouts, exact-commit publication, and independent review
remain appropriate for official compatibility claims. They are not
prerequisites for asking exploratory questions of the local VM.
