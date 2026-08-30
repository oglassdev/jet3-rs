# DAO oracle protocol 1.2.0 (differential read contract)

Protocol 1.2.0 is the portable scenario-inventory and snapshot contract for
the DAO read differential (#98). It executes no DAO operation, interprets no
MDB byte, and establishes no compatibility by itself.

## Documents

| File | `document_type` | Purpose |
| --- | --- | --- |
| `scenarios.schema.json` | `dao_scenario_inventory` | Closed field set and generator grammar for every scenario. |
| `scenarios.json` | `dao_scenario_inventory` | The declarative DAO-versus-Rust read inventory, built reproducibly by `scripts/build_v1_2_inventory.py`. |
| `branch-registry.schema.json`, `branch-registry.json` | `dao_branch_registry` | Closed list of Rust reader coverage branch ids that a `coverage.json` receipt may cite. |
| `coverage-receipt.schema.json` | `coverage_receipt` | Shape of the `coverage.json` the Rust producer writes beside its snapshot. |
| `canonical-semantic-snapshot.schema.json` | `canonical_semantic_snapshot` | Shape of the canonical snapshot both producers emit. |

`scripts/validate_protocol_v1_2.py schemas` lints every schema;
`inventory <path>` validates the inventory; `document <path>` validates one
snapshot, coverage receipt, or registry document; and
`pair <coverage> [snapshot]` verifies that the artifacts describe the same
reader run. Opening failures have no snapshot argument.

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
  other modes are rejected until their protocol revisions are defined.
- `expected_outcome: expected_error` requires an `error_class`; `success`
  requires `error_class: null`. Negative opening scenarios generate Jet 4,
  encrypted, or password-protected databases that the Rust reader must reject
  with a structured error.
- `boundary` names the physical dimension a scenario targets and whether it
  sits below, at, or above it. Boundary cases exist only where a threshold is
  recorded: the extended-slot trio uses the 16,352-page type-05 bitmap span
  recorded by `EXP-0057` through the DAO-side `insert_until_page_count`
  primitive. The below and at cases must attain their target page count
  exactly. The above case stops at the first closed file whose page count
  reaches or exceeds its target because DAO may skip intermediate closed page
  counts; `EXP-0063` records a 16,352-to-16,361 jump for the calibration
  recipe. The trio is classified against the slot-0 capacity; the below/at cases forbid
  `allocation.extended_slot`, and the above case requires it. Step 2 receipts
  must enforce both required and forbidden branch sets.
  Memo/OLE cases use the `EXP-0061` controls (32 inline, 512 single-page,
  2,048 and 4,096 chained) as controls, not as thresholds.
- `required_branches` lists only branches that recorded provenance ties to the
  case. Cases at unrecorded sizes (for example the 32,769-byte maximum) name
  no storage-form branch; a coverage receipt may report more than required
  except where a boundary explicitly lists `forbidden_branches`.
- **Completeness is checked, not counted.** `validate_protocol_v1_2.py`
  encodes the required read set (`REQUIRED_SCENARIOS`) as exact
  generated scenario objects, not merely ids. Every requirement is either
  present without semantic drift or listed in the
  inventory's `deferred_requirements` with the provenance it needs; a silent
  omission fails validation, and `inventory --complete` rejects any deferral.
  The completed read bundle must validate with `--complete`. Current
  deferrals: the largest supported database size, the inline usage-map
  capacity (only an A3 design example, not an observation), extended slots
  beyond ordinal 1, and CP1251 text (`EXP-0061` did not establish it).
- `expected_snapshot_sha256` is `null` until an accepted DAO run records the
  DAO snapshot digest; this inventory contains no observations.
- `preserve_paths` is empty for read scenarios; update legs use it later.

The generator recipe is a closed grammar of DAO steps (`create_database`,
`create_table`, `create_relationship`, `insert_rows`,
`insert_until_page_count`, `grow_rows`, `delete_rows`, `drop_table`,
`reopen`, `close_database`). `grow_rows` updates a bounded prefix of existing
rows in place so scenarios can exercise the observed overflow-pointer path.
Values carry an explicit encoding per DAO type so the DAO producer can marshal
them exactly and the expected typed snapshot value is unambiguous. Unknown
steps, types, or encoding/type combinations fail closed.

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
  relationship fields must name declared columns, and every comparable typed
  value in rows or property maps except `null` and `boolean` (which occupy no
  field bytes) must carry `raw_hex`. Fixed-width row values must carry exactly
  the DAO type's physical width, and text/memo values must identify their
  `code_page`. Two producers can therefore only agree on a complete model.
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
`coverage.json` bound to the source database SHA-256: the registry branch ids
the reader exercised and, for every inventory scenario, whether the observed
outcome and branch set satisfy it. Both files come from one command:

```sh
cargo run -p jet3-cli -- snapshot <file.mdb> --out <dir> --scenario <DAO-READ-...>
```

When the reader rejects the header (`unsupported_version`,
`encrypted_database`, `password_protected`) only `coverage.json` is written.

## Portable commands

```sh
python3 -B oracle/windows-dao/scripts/build_v1_2_inventory.py --check
python3 -B oracle/windows-dao/scripts/validate_protocol_v1_2.py schemas
python3 -B oracle/windows-dao/scripts/validate_protocol_v1_2.py inventory oracle/windows-dao/protocol/v1_2/scenarios.json
python3 -B oracle/windows-dao/scripts/dao_read_diff.py plan oracle/windows-dao/acquisition/read-v1_2.plan.json .
python3 -B oracle/windows-dao/scripts/dao_read_diff.py synthetic-dry-run /tmp/jet3-dao-read-dry-run.json
python3 -B -m unittest discover -s oracle/windows-dao/tests -p 'test_protocol_validation.py' -v
```

These documents are experiment inputs. They record no observation and move no
capability; matrix transitions happen only after an accepted DAO differential.
