# jet3-cli

`jet3-cli --help` lists the existing `probe`, `inspect` and protocol `snapshot`
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
ASCII table name for definition and row inspection; the page/catalog inventory
still describes the file. Text values use the selected code page (1252 by default,
or 1251). Non-ASCII metadata names retain their raw hexadecimal representation.
`--page` cannot be combined with `--table` or `--rows`.

A complete requested inspection returns `ok: true` and exit 0. If a table,
allocation map, row or field cannot be decoded, available diagnostic output is
retained on stdout with `ok: false`, an `issues` array and exit 1. Opening, catalog
or selection failures instead produce a JSON `inspect_failed` error on stderr
with exit 1. Invalid arguments produce JSON errors on stderr with exit 2. A
successful inspection describes the requested decoded content; it is not a
whole-file compatibility verdict. Inspection never modifies the database.

The `create` output path must not exist. Creation uses the library's atomic publication,
validation and default resource limits; publication currently requires Unix.
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

Names and string text must be ASCII. For non-ASCII text use already-encoded
byte arrays; the CLI does not silently encode UTF-8 into the database. All
byte array elements must be integers from 0 through 255. The CLI exposes no
raw Memo/OLE reference headers; the library allocates payload references.

An optional top-level `relationship` selects a relationship creation API:

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
`jet3` library. It provides no update/delete commands or compatibility claim.
