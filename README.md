# jet3-rs

An original, clean-room Rust library and toolset for unencrypted Access 97 /
Jet 3 `.mdb` files.

This repository is in its foundation phase. It contains format-neutral checked
binary I/O and resource-bound primitives plus a versioned DAO evidence
protocol. No MDB reading, writing, validation, or Microsoft DAO compatibility
capability is implemented or claimed yet.

## Workspace

- `crates/jet3`: safe public library
- `crates/jet3-cli`: diagnostic and inspection command-line program
- `crates/jet3-testkit`: fixture and semantic-comparison support
- `oracle/windows-dao`: Windows-only independent DAO test oracle
- `docs/validation`: measurable requirements and evidence rules

## Start here

```sh
./scripts/acceptance.sh quick
```

The eventual complete acceptance gate has a stable entry point:

```sh
./scripts/acceptance.sh full
```

During bootstrap, `full` intentionally exits nonzero with `BLOCKED` after the
wired checks; missing release gates are never treated as a pass.

See [validation/README.md](docs/validation/README.md) for the distinction
between currently wired bootstrap checks and the full v1 acceptance contract.

## Status

Jet-specific format capabilities remain **unimplemented and unverified** until
supported by evidence recorded in the validation matrix and accepted by
Microsoft DAO where applicable. The bounded-input safety and format-neutral
atomic-publication foundations are tracked separately as partial,
internal-only work and are not format compatibility claims.
