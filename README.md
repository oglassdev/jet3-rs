# jet3-rs

A clean-room, safe Rust library and CLI for reading and writing unencrypted
Microsoft Access 97 (Jet 3) `.mdb` files.

- No runtime dependency on Access, DAO, ODBC, Java or native libraries.
- `unsafe` is forbidden in the library. Malformed input returns structured
  errors, and all work is bounded by a caller-owned resource budget.
- Every format fact is cited in [`docs/PROVENANCE.md`](docs/PROVENANCE.md).
  Microsoft DAO on Windows is used only as a black-box test oracle.

> **Status: pre-v1.** Reading, creation and updates are implemented, but only
> the specific scenarios recorded in `PROVENANCE.md` are verified against DAO.
> v1 release verification is still open
> ([#370](https://github.com/oglassdev/jet3-rs/issues/370)).

## What it does

**Read**
- Open a file, list tables, columns and properties, stream rows and decode
  every Jet 3 value type, including Memo/OLE.
- Traverse indexes and read relationships.
- Validate a whole file: rows, values, index membership, keys, relationships
  and page allocation.

**Create**
- Create a database from tables, columns, indexes, initial rows and
  relationships.
- AutoIncrement, Memo/OLE, and up to 32 indexes per table, each with up to
  ten components in either direction.
- Scalar and composite relationships, including cascade, one-to-one and
  unenforced forms.
- Column and table properties: Required, AllowZeroLength, DefaultValue,
  ValidationRule/Text and Description, stored as text.
- The same request always produces the same bytes.

**Update existing files**
- Insert, update, replace and delete rows. Indexes, Memo/OLE payloads and
  relationship constraints and cascades are maintained.
- Schema edits: create, rename and drop tables, columns and indexes; change
  Required/AllowZeroLength; create, drop and replace relationships.
- Unrelated data is preserved, including objects the library doesn't
  interpret, such as saved queries.
- Changes are published atomically, and a refused change leaves the file
  unchanged.

**Not supported**
- Anything other than Jet 3: Jet 4, ACCDB, encrypted or password-protected
  files.
- Forms, reports, macros, VBA, query execution, replication and multi-user
  locking.
- Creating databases with a sort order other than General. Existing
  General, Nordic, traditional Spanish, Dutch, Cyrillic and Greek databases
  are writable; other sort orders are read-only.
- Evaluating validation rules or defaults, in-place column type changes,
  indexes on Memo/OLE, and rows that would need multi-hop overflow.

Unsupported requests are refused with a structured error rather than
approximated. [`docs/plans/V1_SCOPE.md`](docs/plans/V1_SCOPE.md) has the full
scope.

## Quick start

### CLI

```sh
cargo install --path crates/jet3-cli

jet3-cli inspect example.mdb --table Items --rows   # schema and rows as JSON
jet3-cli validate example.mdb                       # whole-file check
jet3-cli create new.mdb --input tables.json         # create from a JSON request
jet3-cli mutate new.mdb --input insert.json         # insert/update/replace/delete
jet3-cli schema new.mdb --input edit.json           # schema edits
```

```json
{"operation": "insert", "table": "Items", "values": [{"long": 4}, {"text": "New"}]}
```

See the [CLI guide](crates/jet3-cli/README.md) for every request shape.

### Library

```rust
use std::num::NonZeroU8;
use jet3::{
    ColumnSpec, ColumnType, DatabaseSpec, IndexColumnSpec, IndexKind, IndexSpec,
    ResourceBudget, ResourceLimits, TableRows, TableSpec, TableValidation, create_database,
};

const NAME_LEN: NonZeroU8 = NonZeroU8::new(50).unwrap();
let columns = [
    ColumnSpec::new(b"Id", ColumnType::AutoIncrement),
    ColumnSpec::new(b"Name", ColumnType::Text { max_len: NAME_LEN }),
];
let indexes = [IndexSpec {
    name: b"PrimaryKey",
    fields: &[IndexColumnSpec::ascending(b"Id")],
    kind: IndexKind::Primary,
}];
let people = TableSpec {
    name: b"People",
    columns: &columns,
    indexes: &indexes,
    validation: TableValidation::NONE,
};
let mut budget = ResourceBudget::new(ResourceLimits::default());
let spec = DatabaseSpec { tables: &[TableRows::empty(people)], ..DatabaseSpec::default() };
create_database("people.mdb", &spec, &mut budget)?;
```

Reading starts at `DatabaseReader`. Row writes are `insert_row`,
`update_field`, `update_row` and `delete_row`, and schema changes go through
`edit_schema`. Run `cargo doc -p jet3 --open` for the API docs.

## Repository layout

| Path | Contents |
| --- | --- |
| `crates/jet3` | The library |
| `crates/jet3-cli` | Command-line front end |
| `crates/jet3-testkit` | Test fixtures and semantic snapshots |
| `oracle/windows-dao` | DAO differential suites (Windows VM, optional) |
| `docs/PROVENANCE.md` | Source of every format fact and DAO result |
| `docs/validation` | Support matrix and release gates |

## Development

Tools are pinned in `mise.toml` (see [TOOLING.md](docs/TOOLING.md)).

```sh
mise install
just          # list recipes
just ready    # fmt, clippy, tests, docs and repository checks: run before a PR
```

Contributor rules are in [AGENTS.md](AGENTS.md). Work is tracked in
[GitHub issues](https://github.com/oglassdev/jet3-rs/issues). DAO runs need a
local Windows VM; see [LOCAL_WINDOWS_VM.md](docs/LOCAL_WINDOWS_VM.md).

## License

MIT OR Apache-2.0.
