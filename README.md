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
inventory. Creation packs tables and multi-page system catalogs with inline
and indirect allocation maps. Table and column names admit 64 Windows-1252 bytes;
index names admit 63. Linked table definitions support
first and later tables, initial rows, and indexes with independent map pages.
It supports initial rows, explicit/generated AutoIncrement IDs, independent
Memo/OLE columns, up to 32 scalar indexes (including Date, Binary, fixed/variable
Text and GUID), and enforced Long relationship graphs within each table's
32-logical-index capacity. Creation handles
multiple parents or children, chains, self-references and shared foreign indexes,
with nullable child keys, Long/AutoIncrement parents, column options and payloads. Existing-file mutations include row insertion/deletion,
row replacement with stable locators across overflow growth and collapse,
Memo/OLE payload allocation and reuse, generated IDs, and
index maintenance for those key types with one to ten components per index. The accepted payload
lifecycles include native DAO continuations and Rust mutation of native files.
Related tables admit inserts, field/full-row updates and deletion for enforced,
non-cascading Long relationships, including multiple constraints and self-references.
Nullable foreign keys and shared foreign indexes are supported. Atomic
self-reference changes follow physical index order: if the foreign index precedes
the parent index, the child key must exist before the edit.
Related-row payloads retain the normal Memo/OLE mutation bounds. Orphan writes
and changes to referenced parent keys are refused before publication.

Creation and updates remain partial: schema combinations, index key types,
allocation, and relationship mutation are restricted. Publication supports Unix
and Windows. Windows flushes the file before publication without a separate
directory-sync guarantee.
Creation produces identical MDB bytes for the same ordered request and library
version, independently of the destination path and successful resource limits.
The read-only library and CLI validator check user/system table rows, values, index
membership and supported scalar key semantics within a shared budget. It checks
key completeness, null rules, uniqueness and branch bounds, and reports indexes
whose key schemas remain uninterpreted.
It also checks catalogued allocation ownership, availability-map membership,
and unique reachability of rows and live Memo/OLE fragments, including catalog
property payloads. Enforced Long
relationships get reciprocal-metadata and parent/child key checks; unsupported
forms are counted separately. Saved-query
definitions and their system storage survive the recorded native-input row
mutation suites unchanged; query execution is outside v1.
Atomic publication and rollback verification remain internal-only. Local and
hosted DAO differential runs establish evidence for their recorded capabilities
and source revisions. AutoIncrement comparisons include explicit IDs, negative
IDs and signed-boundary wrap, with failed Rust requests preserving the source.

See the [current checkpoint and remaining work](docs/plans/V1_SCOPE.md#current-checkpoint)
for exact evidence boundaries and the GitHub issues tracking completion.
