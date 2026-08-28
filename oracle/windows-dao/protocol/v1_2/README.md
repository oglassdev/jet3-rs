# DAO oracle protocol 1.2.0 (differential read contract)

Protocol 1.2.0 is the portable scenario-inventory and snapshot contract for
the P8 differential read program (`IMPLEMENTATION_PLAN.md` Sections 5.1 and
5.2). It executes no DAO operation, interprets no MDB byte, and establishes no
compatibility. Protocols 1.0.0 and 1.1.0 remain frozen under `protocol/v1` and
`protocol/v1_1`.

## Documents

| File | `document_type` | Purpose |
| --- | --- | --- |
| `scenarios.schema.json` | `dao_scenario_inventory` | Closed field set and generator grammar for every scenario. |
| `scenarios.json` | `dao_scenario_inventory` | The declarative DAO-versus-Rust read inventory, built reproducibly by `scripts/build_v1_2_inventory.py`. |
| `branch-registry.schema.json`, `branch-registry.json` | `dao_branch_registry` | Closed list of Rust reader coverage branch ids that a `coverage-receipt.json` may cite. |
| `canonical-semantic-snapshot.schema.json` | `canonical_semantic_snapshot` | Shape of the canonical snapshot both producers emit. |

`scripts/validate_protocol_v1_2.py schemas` lints every schema;
`inventory <path>` validates the inventory; `document <path>` validates one
snapshot or registry document.

## Scenario inventory

Every scenario has exactly `id`, `content_sha256`, `capability_ids`,
`boundary`, `operation`, `generator_recipe`, `required_branches`,
`expected_snapshot_sha256`, and `preserve_paths`. Fields that do not apply are
`null` or an empty array; there are no conditional members.

- `content_sha256` is SHA-256 over the canonical UTF-8 JSON (sorted keys,
  compact separators, one trailing LF) of the scenario object with
  `content_sha256` removed. The validator recomputes it, so any semantic edit
  requires a new hash and a changed `id` is a new scenario.
- `capability_ids` must exist in `docs/validation/support-matrix.json`;
  `required_branches` must exist in `branch-registry.json`.
- `operation.mode` follows the protocol 1.0 prefix rule: `DAO-READ-*` is
  `rust_read_dao`, `DAO-WRITE-*` is `dao_open_rust`, `DAO-UPDATE-*` is
  `dao_verify_rust_update`. This revision enables only `rust_read_dao`; the
  other modes are rejected until the P10 write/update revision.
- `expected_outcome: expected_error` requires an `error_class`; `success`
  requires `error_class: null`. Negative opening scenarios generate Jet 4,
  encrypted, or password-protected databases that the Rust reader must reject
  with a structured error.
- `boundary` names the physical dimension a scenario targets and whether it
  sits below, at, or above it. Boundary cases exist only where a threshold is
  recorded: the extended-slot trio uses the 16,352-page type-05 bitmap span
  from `SRC-0020`/`EXP-0057` through the DAO-side `insert_until_page_count`
  primitive, which inserts rows until the file page count reaches the target.
  Memo/OLE cases use the `EXP-0061` controls (32 inline, 512 single-page,
  2,048 and 4,096 chained) as controls, not as thresholds.
- `required_branches` lists only branches that recorded provenance ties to the
  case. Cases at unrecorded sizes (for example the 32,769-byte maximum) name
  no storage-form branch; a coverage receipt may report more than required.
- **Completeness is checked, not counted.** `validate_protocol_v1_2.py`
  encodes the plan's named minimum read set (`REQUIRED_SCENARIOS`) as exact
  scenario ids. Every requirement is either present or listed in the
  inventory's `deferred_requirements` with the provenance it needs; a silent
  omission fails validation, and `inventory --complete` rejects any deferral.
  The P8 step-4 read bundle must validate with `--complete`. Current
  deferrals: the largest supported database size, the inline usage-map
  capacity (only an A3 design example, not an observation), extended slots
  beyond ordinal 1, and CP1251 text (`EXP-0061` did not establish it).
- `expected_snapshot_sha256` is `null` until an accepted DAO run records the
  DAO snapshot digest through the P8T mechanism; this inventory contains no
  observations.
- `preserve_paths` is empty for read scenarios; update legs use it in P10.

The generator recipe is a closed grammar of DAO steps (`create_database`,
`create_table`, `create_relationship`, `insert_rows`,
`insert_until_page_count`, `delete_rows`, `drop_table`, `reopen`,
`close_database`). Values carry an explicit encoding per DAO type so the DAO
producer can marshal them exactly and the expected typed snapshot value is
unambiguous. Unknown steps, types, or encoding/type combinations fail closed.

## Snapshot contract

The snapshot keeps the 1.0/1.1 typed-value model and `jet3-testkit`
canonical JSON rules (sorted object keys, compact separators, one trailing LF,
lossless `raw_hex` beside converted forms). Differences from 1.1:

- **Row identity.** `canonical_key` is the lowercase SHA-256 of the canonical
  JSON bytes of the row's `values` object, and `duplicate_ordinal` counts
  byte-identical value objects from zero. Rows are ordered by
  `canonical_key` then `duplicate_ordinal`. Both producers derive the key from
  schema plus values alone; no physical row id, primary key, or producer
  choice is involved, and tables without a primary key need no special rule.
- **Model integrity and lossless raw are enforced.** Each row's `values`
  keys must equal the table's declared column names exactly, every typed
  value kind must match its column's DAO type (or be `null`), index fields and
  relationship fields must name declared columns, and every converted row
  value except `null` and `boolean` (which occupy no field bytes) must carry
  `raw_hex`. Two producers can therefore only agree on a complete model.
- **Unavailable schema facts are not part of the compared model.** Column
  `nullable`/`required` and index `ignore_nulls` are removed from the
  canonical column and index objects because the recorded provenance shows
  they are not decodable from the observed physical records. A producer that
  knows them (DAO) reports them under `producer_extensions`, keyed by the
  semantic JSON pointer of the object they describe. Nothing in the compared
  model may be guessed.
- **Comparison projection.** `comparison_projection` fixes the JSON pointers
  removed before byte comparison: `/producer` (kind and revision necessarily
  differ) and `/producer_extensions`. Everything else, including array order,
  object membership, `raw_hex`, and `raw_preservation`, is compared
  byte-for-byte after projection.
- The 1.1 per-array size caps are lifted; bounds come from the scenario
  recipe and the reader's resource limits.

DAO never emits allocation internals. The Rust producer additionally emits
`coverage-receipt.json` (P8 step 2) bound to the source database SHA-256 and
listing only registry branch ids; its schema is added in that step.

## Portable commands

```sh
python3 -B oracle/windows-dao/scripts/build_v1_2_inventory.py --check
python3 -B oracle/windows-dao/scripts/validate_protocol_v1_2.py schemas
python3 -B oracle/windows-dao/scripts/validate_protocol_v1_2.py inventory oracle/windows-dao/protocol/v1_2/scenarios.json
python3 -B -m unittest discover -s oracle/windows-dao/tests -p 'test_protocol_validation.py' -v
```

These documents are experiment inputs. They record no observation and move no
capability; matrix transitions happen only through the explicit P8 step-4
allowlist after an accepted exact-commit DAO bundle.
