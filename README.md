# jet3-rs

An original, clean-room Rust library and toolset for unencrypted Access 97 /
Jet 3 `.mdb` files.

The library opens unencrypted Jet 3 files, enumerates schema, streams rows,
decodes values including Memo/OLE, and traverses indexes. It also provides
bounded database creation and existing-file insert, update, and delete APIs.
A CLI exposes inspection, typed JSON creation/mutation requests, and semantic
snapshots; see [its usage guide](crates/jet3-cli/README.md).

Development is ongoing. DAO differential evidence covers specific recorded
reader, creation, and update scenarios; it does not establish full Jet 3
compatibility or completion of the v1 release gates.

## Workspace

- `crates/jet3`: safe public library
- `crates/jet3-cli`: inspection, creation, mutation, and snapshot commands
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

The reader has hosted DAO differential evidence for its documented capability
inventory. Creation includes up to four tables, initial rows, bounded numeric
indexes, generated AutoIncrement IDs, Memo/OLE payloads, and a restricted
two-table relationship construction. Existing-file mutations include bounded
row insertion/deletion, scalar replacement, and unique Long index maintenance.

Creation and updates remain partial: schema combinations, index key types,
allocation, and relationship mutation are restricted. Publication is Unix-only.
General database validation is not implemented, and atomic failure/rollback
verification remains internal-only. Local DAO experiments provide development
evidence; only hosted results feed the support matrix.

See the [current checkpoint and remaining work](docs/plans/V1_SCOPE.md#current-checkpoint)
for exact evidence boundaries and the GitHub issues tracking completion.
