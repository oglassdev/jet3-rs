# jet3-cli

`jet3-cli --help` lists `probe`, `inspect`, `validate`, `mutate` and protocol `snapshot`
commands. `create` is an optional JSON frontend to the public creation APIs:

```sh
jet3-cli create example.mdb --input request.json
cat request.json | jet3-cli create example.mdb --input -
```

Inspect metadata and optional rows as JSON:

```sh
jet3-cli inspect example.mdb
jet3-cli inspect example.mdb --table Items --rows --code-page 1252
jet3-cli inspect example.mdb --page 0 --hex
```

`inspect` reads pages through the library's file reader, with a 256 MiB input
limit. Its existing `pages`, `catalog`, `tables` and raw diagnostic fields remain
available. Table entries include their catalog names. `--table` selects an exact
Unicode table name for definition and row inspection; the page/catalog inventory
still describes the file. Text values and metadata names use the selected code
page (1252 by default, or 1251). Names containing undefined bytes retain their
raw hexadecimal representation.
`--page` cannot be combined with `--table` or `--rows`.

A complete requested inspection returns `ok: true` and exit 0. If a table,
allocation map, row or field cannot be decoded, available diagnostic output is
retained on stdout with `ok: false`, an `issues` array and exit 1. Opening, catalog
or selection failures instead produce a JSON `inspect_failed` error on stderr
with exit 1. Invalid arguments produce JSON errors on stderr with exit 2. A
successful inspection describes the requested decoded content; it is not a
whole-file compatibility verdict. Inspection never modifies the database.

Validate reachable user and system table data without modifying the file:

```sh
jet3-cli validate example.mdb --code-page 1252
jet3-cli validate example.mdb --max-input-bytes 268435456 --max-work-units 1000000000
```

`validate` calls `DatabaseReader::validate` with one shared resource budget. It
decodes active catalog records and table definitions, checks live row counts,
decodes every field, streams reachable Memo/OLE values to their end, and traverses
each physical index using the existing index reader. Text uses Windows-1252
by default; Windows-1251 is also accepted. The input limit defaults to 256 MiB;
the other library limits remain in effect alongside the optional work limit.
Exclude concurrent writers while validation runs.

Success returns `ok: true`, `scope: "catalogued_allocations_and_tables"`, checked counts,
coverage limits and resource usage on stdout with exit 0. Long-value bytes count
raw payload bytes per reference, before text decoding. The first failure returns
`validation_failed` JSON on stderr with exit 1 and no success report; its message
includes the table and available field/index/row context. Invalid arguments exit 2.

Counts include both user and system tables. Validation checks row counts and values,
unique row/payload reachability,
index live-row membership, supported scalar keys and branch bounds, and catalogued
allocation ownership. Enforced single ascending Long relationships check reciprocal
metadata, parent uniqueness and non-null child-key inclusion. The JSON report
counts unsupported index schemas and relationship catalog rows separately; complete
relationship inventory is checked only when all central rows are interpreted.
Non-table object contents, unreferenced pages and allocation slack remain outside
these checks. Catalog property payloads are checked as binary values; their
application-specific property grammar is not interpreted.
Success does not establish Access/DAO compatibility.

The `create` output path must not exist. Creation uses the library's atomic publication,
validation and default resource limits on Unix and Windows. Windows flushes file
contents before publication without a separate directory-sync guarantee.
Success writes one JSON object to stdout. Invalid command arguments exit 2;
invalid JSON or a refused creation exits 1 with a JSON error on stderr. Unknown
JSON fields are rejected. No existing database is modified by this command.

A minimal request creates an empty database: `{"tables": []}`. A table request:

```json
{
  "tables": [{
    "name": "Items",
    "columns": [
      {"name": "Id", "type": "auto_increment"},
      {"name": "Label", "type": "text", "size": 40}
    ],
    "indexes": [{
      "name": "ById", "kind": "primary",
      "fields": [{"column": "Id"}]
    }],
    "rows": [
      ["auto_increment", {"text": "First"}],
      ["auto_increment", null]
    ]
  }]
}
```

Tables and columns retain their supplied order; row cells are positional.
`indexes` and `rows` default to empty arrays. Index `kind` is `primary`, `unique`
or `ordinary`. Each field references an exact column name and has optional
`direction`: `ascending` (default) or `descending`. Other schema combinations
and limits are checked by the library, including which types may be indexed.

Column types are `boolean`, `byte`, `integer`, `long`, `auto_increment`,
`currency`, `single`, `double`, `date_time`, `guid`, `text`, `fixed_text`,
`binary`, `memo` and `long_binary`. `text`, `fixed_text` and `binary` require
`size` from 1 through 255; other types do not accept `size`. Fixed text has an
exact byte length; variable text and binary use the maximum byte length.

Row cells use JSON `null`, the string `"auto_increment"`, or a single typed
value object. The tag must match the column type (fixed text uses `text`):

| Cell | Meaning |
| --- | --- |
| `{"boolean": true}` | Boolean; null is also false in Jet 3 |
| `{"byte": 255}`, `{"integer": -32768}`, `{"long": 42}` | Unsigned 8-bit, signed 16-bit and signed 32-bit integers |
| `{"currency": 12345}` | Exact signed 64-bit integer scaled by 10,000, here 1.2345 |
| `{"single": 1.25}`, `{"double": -2.5}` | Floating-point values |
| `{"date_time": 36526.0}` | OLE Automation day count |
| `{"text": "Hello"}`, `{"memo": "Long text"}` | ASCII text encoded without conversion |
| `{"text": [233]}`, `{"memo": [233]}` | Explicit database-code-page bytes, here Windows-1252 é |
| `{"binary": [0, 255]}`, `{"long_binary": [0, 255]}` | Exact binary/OLE bytes |
| `{"guid": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15]}` | Sixteen bytes in conventional GUID display order |

Names are Unicode JSON strings encoded strictly as Windows-1252. Table and
column names allow up to 64 encoded bytes; index and relationship names allow
63. Names preserve their supplied bytes, including trailing spaces. Names with
equal English-US/CP1252 collation keys collide (for example, `AE` and `Æ`).
Controls, undefined bytes, leading ASCII spaces and `. ! [ ]` or backtick are
refused. References to tables and columns use their exact supplied names.

String text values must be ASCII. For non-ASCII text use already-encoded
byte arrays; the CLI does not silently encode UTF-8 into the database. All
byte array elements must be integers from 0 through 255. The CLI exposes no
raw Memo/OLE reference headers; the library allocates payload references.

Text and Memo columns accept `"allow_zero_length": true` to permit present-empty
values. Fixed Text also accepts the property but requires exactly its declared
byte width in row inputs. The default is false; other types reject this option.

Columns accept `"required": true` to reject stored nulls during creation,
insertion and replacement. Empty Binary/OLE values store null and therefore
also fail this constraint. Boolean null inputs store false. AutoIncrement
ignores Required and retains false; use `"auto_increment"` to generate a value.
Required and AllowZeroLength are independent, so required Text/Memo can admit
present-empty values when both options are true.

An optional top-level `relationship` selects the two-table relationship API:

```json
{
  "name": "ParentChild",
  "parent": {"table": "Parent", "column": "Id"},
  "child": {"table": "Child", "column": "ParentId"}
}
```

Place that object alongside `tables`. Supply the parent primary index and
matching parent/child rows in the table requests; the library creates the
foreign index and validates its current relationship bounds. When all rows are
empty, the CLI uses the schema-only API, including its additional supported
index layouts; otherwise it uses the initial-row API. This interface
adds no relationship, index, schema or payload support beyond the linked
`jet3` library. It makes no compatibility claim beyond the underlying library and its recorded evidence.

For multiple or self-referencing relationships, use `"relationships": [...]`
with an array of the same objects. The array currently admits at most two
enforced, non-cascading single-Long constraints and any supported unrelated
tables. Parent keys may be Long or AutoIncrement and need an ascending unique
index; the first eligible index in logical name order is selected. Foreign
columns must be Long. Existing ordinary ascending FK indexes are reused, and
each relationship alias consumes a slot within the 32-logical-index limit.
Supply only one of `relationship` and `relationships`. An empty array creates
ordinary tables.

`mutate` applies one public row operation to an existing database:

```sh
jet3-cli mutate example.mdb --input request.json
```

Use `--input -` for stdin. Requests have one of these shapes:

```json
{"operation":"insert","table":"Items","values":[{"long":4},{"text":"New"}]}
```

```json
{"operation":"update","table":"Items","row":{"page":23,"slot":1},"column":0,"value":{"long":42}}
```

```json
{"operation":"delete","table":"Items","row":{"page":23,"slot":1}}
```

For complete row replacement, including variable Text/Binary widths, null
transitions and Boolean values, supply every column in schema order:

```json
{"operation":"replace","table":"Items","row":{"page":23,"slot":1},"values":[{"long":42},{"text":"Renamed"}]}
```

Page/slot locators come from the public row reader; column ordinals come from
its table definition. They describe the unchanged source, not a primary key or
row position. The CLI resolves the exact supplied table and locator with that
reader before an update/replace/delete. Names must be losslessly representable
in CP1252, and values use the same typed
JSON cells as creation. There is no batch, implicit retry or schema conversion. Each accepted request invokes its public mutation API once
with the default library resource budget.

Success returns JSON with `ok`, `operation`, `file` and `row` (the new locator
for insertion; the addressed locator for update/deletion). Failures return
`mutation_failed` JSON on stderr and exit 1; invalid command arguments exit 2.
`publication_stage`, when present, identifies a library publication failure.
A sync error after publication can mean the change is already visible: do not
blindly retry a failed mutation. Exclude concurrent writers for the entire
operation. Publication is available on Unix and Windows; Windows has no separate
directory-sync guarantee.

Field updates support present fixed values. Complete row replacement supports
scalar values and independent Memo/OLE payloads while keeping the row on its
current page. Insertion can reuse released pages or append within inline maps.
Deletion compacts retained pages or releases a page containing its last live row.
Up to three numeric indexes support one/two components, multiple levels, mixed
directions, duplicates and null policies. Memo/OLE mutation accepts nonempty
typed payloads or null, and reuses released payload storage.

An AutoNumber insertion accepts `"auto_increment"` or an explicit `{"long": 42}`.
For replacement, supply `"auto_increment"` to keep the existing ID, or its
unchanged Long value. Deletion retains the generation state. Rejected requests
preserve the whole file, including that state; DAO can consume a number on a
failed insert. Relationships, other index key types, indirect maps and cross-page
row replacement remain restricted. The recorded finite DAO comparisons are in
`docs/PROVENANCE.md`; CLI tests do not expand that coverage.
