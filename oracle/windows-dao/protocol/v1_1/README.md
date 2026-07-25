# DAO oracle protocol 1.1.0 (M1 data contract)

Protocol 1.1.0 is a portable plan, snapshot, and comparison contract. It does
not execute DAO, interpret MDB bytes, or establish product compatibility.
Protocol 1.0.0 remains separately frozen under `protocol/v1`; its M0 schemas,
runner, and bundle validation are unchanged.

## Controlled recipes

M1 admits only `DAO-GEN-*` scenarios and these exact recipes:

| Recipe | Controlled content |
| --- | --- |
| `repeat_empty` | Two independent empty `dbVersion30` creations. |
| `binary_marker` | One `dbBinary` field and one row containing the fixed eight-byte marker `0011223344556677`. |
| `text_index_baseline` | One `dbText(8)` field and one `JET3M1` row, without an index. |
| `text_index_nonunique` | The same table and row with one nonprimary, nonunique ascending index. |
| `memo_ladder` | One `dbMemo` field with repeated ASCII `M` at lengths 1, 2047, 2048, 2049, 32767, 32768, and 32769. |
| `long_binary_ladder` | One `dbLongBinary` field with repeated byte `0xa5` at the same lengths. |

Every step is a discriminated object with `additionalProperties: false`.
The portable validator additionally requires the exact action sequence, table
and field order, field type and value order, marker, index flags, and ladder
lengths. Unknown actions, arguments, recipes, and type/value combinations fail
closed.

The examples are generated reproducibly by
`scripts/build_m1_examples.py`. `examples/m1-inventory.json` lists every M1
scenario and pair with its exact SHA-256. Inventory validation rejects a
missing, unlisted, changed, duplicated, or invalid example.

## Exact pair comparisons

`dao_pair` documents declare a comparison kind, ordered scenario sides, and an
exact JSON Pointer allowlist. M1 contains two pair contracts:

- the empty repeats may differ only at `/database_sha256` and `/scenario_id`;
- the `dbText(8)` scenarios may additionally differ at
  `/tables/0/indexes`.

The comparator recursively checks the complete canonical snapshot. It stops at
an allowed path only when the values actually differ. Any difference outside
the allowlist fails, as does an allowed path that did not differ. Array order,
object membership, typed values, and all unlisted metadata are therefore part
of the comparison.

## Reports and immutable bundles

The 1.1 report has independent scenario and pair result lists and independent
counts. A pair side must name a selected scenario. In a passing bundle, each
pair snapshot reference must be byte-for-byte the same file reference used by
the corresponding passing scenario result. The bundle validator also binds:

- directory, manifest, report, clean commit, run ID, and status;
- every retained payload's exact size, SHA-256, role, and report reference;
- scenario ID and recipe to the checked input;
- output database hash to the DAO snapshot;
- snapshot producer and revision to DAO and the bundle commit;
- operation-log action order to the scenario steps and reopen/snapshot
  lifecycle; and
- reported pair differences to a fresh canonical deep comparison.

A passing bundle cannot contain unreferenced payloads.

## Controlled execution boundary

`scripts/run-m1-controlled.ps1` is the reviewed Windows x86 executor for this
exact inventory. It consumes no source MDB, uses only project-generated
`dbVersion30` databases, requires the provider identity recorded by the ready
environment, and applies the `System.Byte[]` marshalling established by
`EXP-0006`. Operation logs carry structured input/readback runtime types,
lengths, SHA-256 values, the exact fixed-binary marker, and normalized
HRESULT/error records.

Every bundle retains `inventory.json` and must contain all seven scenarios and
both pairs in checked order with exact input hashes. The publisher writes into
a private same-volume stage, flushes every payload, independently validates
the identity-shaped stage, rechecks Git and provider state, and commits
visibility with one non-overwriting directory move. Managed .NET cannot fsync
the parent directory, so the contract does not claim power-loss persistence of
that directory entry.

Implementation alone is not evidence. These documents remain experiment inputs
until the executor produces a valid bundle from its exact clean commit. M1
proves only the recorded controlled DAO-generation/readback scenarios; it does
not establish a Rust reader, writer, update operation, or a general
compatibility claim. The older non-executing
`scripts/preflight-m1-controlled.ps1` remains available and still exits
`BLOCKED` before COM activation or output mutation. The checked executor is a
separate entry point.

## Portable commands

```sh
python3 oracle/windows-dao/scripts/build_m1_examples.py --check
python3 oracle/windows-dao/scripts/validate_m1_protocol.py schemas
python3 oracle/windows-dao/scripts/validate_m1_protocol.py document \
  oracle/windows-dao/examples/m1-inventory.json
python3 -m unittest discover -s oracle/windows-dao/tests -v
```

Future immutable 1.1 bundles use the same `bundle` command form as 1.0, but are
validated with `validate_m1_protocol.py`.
