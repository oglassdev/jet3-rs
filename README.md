# jet3-rs

An original, clean-room Rust library and toolset for unencrypted Access 97 /
Jet 3 `.mdb` files.

This repository is in its foundation phase. It contains format-neutral checked
binary I/O and resource-bound primitives, generic Microsoft-published Jet
signature recognition, a typed non-semantic raw-candidate session,
content-agnostic bounded reads and allocation-free sequential streaming of
complete 2-KiB Jet 3 pages, bounded access to the documented contextual header
commit region, and a versioned DAO evidence protocol. It does not yet open,
parse, create, update, or validate an MDB, and no Microsoft DAO compatibility
is claimed.

## Workspace

- `crates/jet3`: safe public library
- `crates/jet3-cli`: diagnostic and inspection command-line program
- `crates/jet3-testkit`: fixture and semantic-comparison support
- `oracle/windows-dao`: Windows-only independent DAO test oracle
- `docs/validation`: measurable requirements and evidence rules
- `docs/plans/V1_SCOPE.md`: what v1 is and is not

## Start here

```sh
./scripts/acceptance.sh quick
```

Validate one DAO differential bundle with the full entry point:

```sh
./scripts/acceptance.sh full path/to/canonical-snapshot.json
```

Without a bundle argument or `JET3_DAO_BUNDLE`, `full` exits nonzero with a
one-line reason.

See [validation/README.md](docs/validation/README.md) for capability status and
the three v1 release gates.
See [TOOLING.md](docs/TOOLING.md) for the pinned mise-managed developer tools
and the remaining host prerequisites.

## Status

Generic Jet header probing, narrow Jet 3 unencrypted/no-password opening, and
raw Jet 3 page transfer or streaming are **experimental**: the support matrix
records them as partial and internal-only. Complete header and page semantics,
allocation maps, database validation, schema, rows, indexes, long values,
creation, and updates remain planned. The raw commit-region reader preserves
every two-byte value; its two documented labels are contextual and cannot
diagnose a database without contemporaneous `.ldb` lock evidence. The
bounded-input safety and format-neutral atomic-publication foundations are also
partial, internal-only work. None of these states is an MDB validity or
compatibility claim.
