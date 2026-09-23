# jet3-cli

`jet3-cli --help` lists `probe`, `inspect`, `validate`, `mutate`, `schema` and protocol `snapshot`
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
raw hexadecimal representation. User tables also include `properties`:
the table `validation_rule`/`validation_text` and, per column ordinal,
`required`, `allow_zero_length`, `default_value`, `validation_rule`,
`validation_text` and `description` exactly as stored (null when absent). DAO
and Rust store a rule assigned to an existing field with one trailing NUL.
`relationships` lists every `MSysRelationships` relationship, including
unenforced ones without index records: names, ordered field pairs,
`raw_attributes`, the decoded `enforced`, cascade and `join` values (`join` uses
the schema request spelling), and whether
writes interpret it. `--page` cannot be combined with `--table` or `--rows`.

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
allocation ownership. Enforced ordered scalar/composite relationships check reciprocal
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
Optional `null_policy` is `include`, `ignore_all_null` or `required`. It defaults
to `required` for primary indexes and `include` otherwise. Primary indexes must
retain `required`. `ignore_all_null` omits a row only when every indexed component
is null; `required` rejects any null component. Nullable unique indexes enforce
uniqueness only for fully present keys.

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

Columns also accept `"default_value"`, `"validation_rule"`, `"validation_text"`
and `"description"` strings, and tables accept `"validation_rule"` and
`"validation_text"`. They are stored as opaque CP1252 text of 1 to 2,048 bytes
without NUL; expressions are never parsed or evaluated. Defaults are not applied:
rows store exactly the supplied values, including null. A table storing a
rule refuses initial rows and later inserts/updates. Binary, OLE and GUID
columns refuse validation properties, and AutoIncrement columns refuse
expressions at creation; set those later with `set_column_properties`.

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

For composite, multiple or self-referencing relationships, use `"relationships": [...]`
with an array of the same objects. The array admits enforced constraints with one
to ten ordered scalar columns within each table's logical-index capacity,
along with any supported unrelated tables. For a composite endpoint, replace
`"column": "Id"` with `"columns": ["First", "Second"]` on both endpoints.
The lists must have the same length and order as the parent unique index;
AutoIncrement parents also qualify. An eligible ascending index is selected in logical name order;
a descending-only parent gets a separate ascending tree with the same null policy,
shared by its relationships. Endpoints must have matching scalar types; Text/Binary
widths may differ and fixed/variable Text may mix. Existing ordinary ascending FK indexes are reused, and
each relationship alias consumes a slot within the 32-logical-index limit.
Supply only one of `relationship` and `relationships`. An empty array creates
ordinary tables. Each array entry can set `"cascade_updates": true` and/or
`"cascade_deletes": true`; both default to false. For example:

```json
{
  "relationships": [{
    "name": "ParentChild",
    "parent": {"table": "Parent", "column": "Id"},
    "child": {"table": "Child", "column": "ParentId"},
    "cascade_updates": true,
    "cascade_deletes": true
  }]
}
```

Place this array alongside the `tables` request. The singular `relationship`
interface retains its non-cascading, inner-join two-table bounds; use the array
for cascades or join types.

`schema` applies one public schema edit to an existing database:

```sh
jet3-cli schema example.mdb --input request.json
```

Use `--input -` for stdin. Index requests have these shapes:

```json
{"operation":"create_index","table":"Items","index":{"name":"ByLabel","kind":"ordinary","null_policy":"ignore_all_null","fields":[{"column":"Label","direction":"descending"}]}}
```

```json
{"operation":"rename_index","table":"Items","index":"ByLabel","name":"ByLabelDescending"}
```

```json
{"operation":"drop_index","table":"Items","index":"ByLabelDescending"}
```

Index definitions use the same fields and defaults as `create`. A new index is
built over existing rows; uniqueness and required-key violations refuse the
edit. Targets use exact names encoded losslessly in Windows-1252. Relationship
indexes are subject to the library's relationship constraints.

Rename a table and its relationship table-name references with:

```json
{"operation":"rename_table","table":"Items","name":"Products"}
```

The table's rows, columns and indexes are retained. To add an empty table:

```json
{
  "operation": "create_table",
  "table": {
    "name": "Notes",
    "columns": [
      {"name": "Id", "type": "auto_increment"},
      {"name": "Title", "type": "text", "size": 40, "required": true},
      {"name": "Content", "type": "memo", "allow_zero_length": true}
    ],
    "indexes": [{"name": "ById", "kind": "primary", "fields": [{"column": "Id"}]}]
  }
}
```

`create_table` uses the same column and index definitions as `create`; `indexes`
defaults to an empty array. Its table object rejects `rows`. Insert rows with
`mutate` after creating the table. Existing tables and their contents are retained.

Rename a column with:

```json
{"operation":"rename_column","table":"Notes","column":"Title","name":"Heading"}
```

Column values, index membership, Required and AllowZeroLength settings are retained.

Append a column using the same definition as `create`:

```json
{"operation":"create_column","table":"Notes","column":{"name":"Summary","type":"memo","allow_zero_length":true}}
```

Existing rows retain their values; the appended column reads as null, or false
for Boolean. AutoIncrement assigns existing rows values from 1 through N;
the next generated value is N+1. Subsequent row updates or inserts can assign
the new column. All creation column types are accepted within the library's bounds.

Remove a column or table with:

```json
{"operation":"drop_column","table":"Notes","column":"Summary"}
```

```json
{"operation":"drop_table","table":"Notes"}
```

Drop a column's indexes first. A referenced parent table cannot be dropped;
dropping a child table also removes its relationships. Remaining columns retain
their storage ordinals, so use the current
definition's ordinals for field updates and its live column order for row values.

Set, change or clear text properties with (an omitted key keeps the stored
value, `null` clears it):

```json
{"operation":"set_column_properties","table":"Notes","column":"Title","default_value":"\"untitled\"","validation_rule":"Is Not Null","validation_text":null,"description":"Heading"}
```

```json
{"operation":"set_table_properties","table":"Notes","validation_rule":"[Title]<>\"\"","validation_text":"Title required"}
```

Existing rows are retained without evaluation. While a nonempty rule is stored,
inserts and updates on the table are refused with the file unchanged; clear the
rule to write rows. Renaming or dropping a column referenced by a table rule
leaves the rule text unchanged, as DAO does.

Change constraints for future writes with:

```json
{"operation":"set_column_options","table":"Notes","column":"Title","required":true,"allow_zero_length":false}
```

Omitted or null options retain their current settings. Existing null and empty
values remain intact; the new settings apply to subsequent writes. AllowZeroLength
applies to Text and Memo. Column type/size and index options require recreation.
Use `replace_index` to rebuild an index atomically, retaining the original if
the new definition fails:

```json
{"operation":"replace_index","table":"Items","index":"ByLabel","replacement":{"name":"UniqueLabel","kind":"unique","fields":[{"column":"Label"}]}}
```

Relationship edits use the same definition as creation, including optional
`cascade_updates`, `cascade_deletes`, and composite endpoint `columns`. They also
accept `"enforce": false` for an unenforced relationship (no indexes, cascades or
key checks; any 1 to 255 key columns) and `"join"`: `inner` (default), `left`, `right` or
`left_and_right`, the Access display join stored in the relationship attributes.
Database creation accepts `join` but not unenforced relationships:

```json
{"operation":"create_relationship","relationship":{"name":"ParentChild","parent":{"table":"Parent","column":"Id"},"child":{"table":"Child","column":"ParentId"}}}
```

```json
{"operation":"replace_relationship","name":"ParentChild","relationship":{"name":"ParentChildCascading","parent":{"table":"Parent","column":"Id"},"child":{"table":"Child","column":"ParentId"},"cascade_updates":true,"cascade_deletes":true}}
```

```json
{"operation":"drop_relationship","name":"ParentChildCascading"}
```

```json
{"operation":"create_relationship","relationship":{"name":"Loose","enforce":false,"join":"left","parent":{"table":"Parent","column":"Code"},"child":{"table":"Child","column":"Note"}}}
```

Enforced creation checks existing rows against the referenced unique key. Replacement
atomically removes and recreates the relationship, allowing a new name, endpoints,
enforcement, join type and cascade settings; refusal preserves the original
relationship. Deletion retains ordinary indexes shared with the relationship.
Dropping a table also drops its relationships unless an enforced relationship
from another table references it; columns named by any relationship cannot be
dropped.

Databases created with a sort order other than General are readable, but
`schema` and `mutate` refuse them with the file unchanged.

Each request calls `edit_schema` once with the default library resource budget.
Success returns `ok`, `operation` and `file` on stdout. Invalid JSON, unknown
fields or refused edits return `schema_failed` JSON on stderr and exit 1;
invalid arguments exit 2. Errors include `publication_stage` when the library
reports a publication failure. Failures before publication retain the original
file; a sync error after publication can mean the change is already visible.
Exclude concurrent writers for the entire operation and check the publication
stage before retrying. CLI tests establish command behavior, not DAO compatibility.

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

Field updates support scalar values, null transitions and independent Memo/OLE
payloads. They explicitly assign only the selected column; complete row replacement
assigns all columns. When enabled, cascade updates assign matching child tuples,
including equal assignments and exact null tuples; cascade deletes remove matching
children recursively. Conflicts with another enforced relationship refuse the
whole operation. All affected rows, indexes and payloads publish together.
Full-row self replacements retain explicit foreign-key values. Without cascade
updates, assigning a referenced parent key can be refused even when unchanged.
Growing rows may move into hidden storage while retaining their
logical locator; selected multi-hop overflow chains remain unsupported.
Insertion reuses released pages or appends pages. Deletion compacts retained pages
or releases a page containing its last live row. Up to 32 indexes support one to
ten scalar components, multiple levels, mixed directions, duplicates and null
policies. Memo/OLE mutation reuses released payload storage.

An AutoNumber insertion accepts `"auto_increment"` or an explicit `{"long": 42}`.
For replacement, supply `"auto_increment"` to keep the existing ID, or its
unchanged Long value. Deletion retains the generation state. Rejected requests
preserve the whole file, including that state; DAO can consume a number on a
failed insert. The recorded finite DAO comparisons are in
`docs/PROVENANCE.md`; CLI tests do not expand that coverage.
