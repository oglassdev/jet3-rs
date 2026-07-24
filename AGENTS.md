# AGENTS.md

## Premise

This repository is a clean-room, original Rust implementation of the
un-encrypted Microsoft Access 97 / Jet 3 database format. Production code must
not depend on other MDB implementations, Microsoft Access, DAO, ODBC, Java, or
native C libraries at runtime. DAO is an optional Windows-only test oracle, not
a product dependency.

Do not study or adapt implementation code from MDB Tools, mdbtools-pure-rs,
Jackcess, UCanAccess, or any other MDB implementation. Record every format
source, experiment, observation, and fixture origin in the provenance records.
Never claim compatibility from a self-read or self-validation result; only an
independent DAO result can move a capability to DAO-verified.

Production Rust must keep `unsafe` forbidden, reject malformed input with
structured errors, bound allocations and work, stream where practical, and
avoid panics. Keep format constants and checked binary operations out of
high-level operations. No production source file may exceed 800 lines.

## Everyday commands

```sh
cargo fmt --all --check
cargo clippy --workspace --all-targets --all-features --locked -- -D warnings
cargo test --workspace --all-targets --all-features --locked
RUSTDOCFLAGS="-D warnings" cargo doc --workspace --all-features --no-deps --locked
./scripts/acceptance.sh quick
./scripts/acceptance.sh full
```

The `full` acceptance command is the stable project contract. Its required
gates and current bootstrap status are defined under `docs/validation/`. It
must report `BLOCKED` until every required gate is actually wired and present.

## Change discipline

- Add provenance before or with code that relies on new format knowledge.
- Add focused tests for every invariant, boundary, and corruption path.
- Preserve unrelated semantic data during updates.
- Prefer typed offsets and checked decoding over scattered numeric offsets.
- Split modules before they approach 800 lines; avoid global mutable state and
  C-shaped APIs.
- Keep generated fixtures reproducible and identify their generator and
  environment.
- Use conventional commit messages such as `feat:`, `fix:`, `test:`, `docs:`,
  `refactor:`, `perf:`, `build:`, and `chore:`.
