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
| `coverage-receipt.schema.json` | `rust_coverage_receipt` | Database- and revision-bound Rust branch coverage plus allocated-page-set identity. |

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
  recorded by `EXP-0057` through the DAO-side `insert_until_page_count`
  primitive. That step must attain the target page count exactly or fail, so
  the trio is classified against the slot-0 capacity rather than a possibly
  overshot file size. The below/at cases forbid `allocation.extended_slot`;
  the above case requires it. Step 2 receipts must enforce both required and
  forbidden branch sets.
  Memo/OLE cases use the `EXP-0061` controls (32 inline, 512 single-page,
  2,048 and 4,096 chained) as controls, not as thresholds.
- `required_branches` lists only branches that recorded provenance ties to the
  case. Cases at unrecorded sizes (for example the 32,769-byte maximum) name
  no storage-form branch; a coverage receipt may report more than required
  except where a boundary explicitly lists `forbidden_branches`.
- **Completeness is checked, not counted.** `validate_protocol_v1_2.py`
  encodes the plan's named minimum read set (`REQUIRED_SCENARIOS`) as exact
  generated scenario objects, not merely ids. Every requirement is either
  present without semantic drift or listed in the
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

## Outcome contract

`snapshot.json` is a canonical outcome, not an assertion that every input
opened successfully. `outcome: success` carries the complete semantic database
members below and requires `error_class: null`. `outcome: opening_failure`
carries only the common scenario, producer, staged-input SHA-256, comparison
projection, and one closed `error_class`; it must not carry tables,
relationships, database properties, ordering, raw preservation, or producer
extensions. A rejected open is therefore never represented as an ordinary
empty database.

The admitted normalized classes are `unsupported_version`,
`encrypted_database`, and `password_protected`, derived respectively from the
Rust reader's exact structured `UnsupportedVersion`,
`EncryptedOrUnsupported`, and `PasswordedOrUnsupported` format variants. No
candidate, signature, geometry, header-read, I/O, resource, or internal error
maps to these classes. The shared validator binds both the snapshot outcome and
the coverage-receipt outcome to the inventory: failure requires an
`expected_error` scenario and its exact `operation.error_class`; success
requires a success scenario.

For an opening failure, `coverage-receipt.json` sets
`allocated_set_sha256: null` because semantic allocation traversal never
occurred, and its branch set is exactly `open.signature_geometry`,
`open.header_page`, and `open.rejected_format`. Successful receipts require a
real allocated-set digest and cannot claim `open.rejected_format`. The two
artifacts retain the same scenario id, staged-input digest, build-bound Rust
revision, outcome, and error class and are published through the same atomic
bundle boundary.

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
  `code_page`, which is exactly integer `1251` or `1252`. For both `text` and
  `memo`, the validator strictly decodes `raw_hex` with that Windows mapping
  and requires exact equality to `value`; unsupported pages, malformed hex,
  undefined bytes, replacement decoding, and mismatches fail closed. The
  shared `fixtures/text-code-page-vectors.tsv` records this rule once for both
  validators. Two producers can therefore only agree on a complete model.
- **Long values compare by logical payload.** For `memo` and `ole`, comparable
  `raw_hex` is exactly the logical payload byte sequence, independent of
  inline, single-page, or chained Jet storage. It excludes the 12-byte Jet
  long-value header, link bytes, page/slot locators, row-directory bytes, and
  all other storage framing. For OLE, `value` and `raw_hex` are the same
  lowercase hexadecimal byte sequence; for Memo, `raw_hex` is the source
  code-page byte sequence represented by `value`. Consequently equal logical
  values have equal typed-value bytes and row hashes even when their physical
  locators differ.
- **Unavailable schema facts are not part of the compared model.** Column
  `nullable`/`required` and index `ignore_nulls` are removed from the
  canonical column and index objects because the recorded provenance shows
  they are not decodable from the observed physical records. A producer that
  knows them (DAO) reports them under `producer_extensions`, keyed by the
  semantic JSON pointer of the object they describe. Nothing in the compared
  model may be guessed.
- **Column size and attributes are normalized before comparison.** The shared
  `fixtures/column-normalization-vectors.tsv` rule fixes scalar sizes at 1, 2,
  4, 8, or 16 bytes, preserves declared Binary/Text sizes 1 through 255, and
  uses zero for LongBinary/Memo. Comparable attributes are fixed `1` or
  variable `2`, plus auto-increment `16`, so only `1`, `2`, and `17` are
  admitted. DAO masks to these bits; Rust retains its Jet raw class and record
  only under comparison-excluded `producer_extensions`.
- **Comparison projection.** `comparison_projection` fixes the JSON pointers
  removed before byte comparison: `/producer` (kind and revision necessarily
  differ) and `/producer_extensions`. Everything else, including array order,
  object membership, `raw_hex`, and `raw_preservation`, is compared
  byte-for-byte after projection.
- **Rust external-header retention.** When Rust reads an external Memo/OLE, it
  retains the exact 12-byte Jet header only in `producer_extensions` as a
  `binary` typed value whose `value` and `raw_hex` are the same 24 lowercase
  hexadecimal digits. Its key is
  `/tables/{table_index}/rows/{row_index}/values/{escaped_column_name}/jet_external_long_value_header`,
  where both indices address the final canonical arrays and the column token
  uses JSON Pointer `~0`/`~1` escaping. This creates one unambiguous association
  with the comparable value while keeping the physical locator outside row
  hashing and byte comparison. The shared validator rejects malformed or
  unresolved associations. DAO does not emit this Rust storage metadata and
  must not expose it as a required comparable fact.
- The 1.1 per-array size caps are lifted; bounds come from the scenario
  recipe and the reader's resource limits.

DAO never emits allocation internals. For successful Rust reads, the additional
`coverage-receipt.json` is bound to the source database SHA-256 and Rust source
revision and lists only closed registry branch ids. Its
`allocated_set_sha256` is SHA-256 over this deterministic byte stream: user
tables in canonical name order, each encoded as the little-endian `u64` UTF-8
name length, name bytes, little-endian `u64` table-definition root, each
traversed owned page as a little-endian `u64`, and a little-endian `u64::MAX`
table terminator. This digest compares the page set actually admitted to
semantic traversal without placing allocation internals in the DAO/Rust
comparison projection.

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
