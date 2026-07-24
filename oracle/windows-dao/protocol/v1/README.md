# DAO oracle protocol 1.0.0

Protocol 1.0.0 is a data contract, not an MDB implementation.

## Documents

| Document type | Schema | Purpose |
| --- | --- | --- |
| `dao_scenario` | `scenario.schema.json` | Declarative database operation sequence and expected semantic outcome. |
| `canonical_snapshot` | `canonical-snapshot.schema.json` | Deterministic semantic database representation emitted independently by DAO or Rust. |
| `dao_environment` | `environment.schema.json` | Exact Windows, runtime, locale, code-page, timezone, and COM provider inventory. |
| `dao_evidence_report` | `evidence-report.schema.json` | Commit-bound execution outcome and per-scenario results. |
| `dao_bundle_manifest` | `bundle-manifest.schema.json` | Immutable file inventory and hashes for one evidence run. |

All schemas use JSON Schema Draft 2020-12 and reject unknown properties.

## Canonicalization

JSON files are UTF-8 without a byte-order mark. Hashes are calculated over the
exact retained bytes. A canonical snapshot producer must:

1. emit object keys in lexicographic Unicode code-point order;
2. emit tables, columns, indexes, relationships, and rows in the order declared
   by the snapshot's `ordering` object;
3. represent binary/OLE data as lowercase hexadecimal and GUIDs as lowercase
   hyphenated text;
4. represent date/time, decimal, and currency values as invariant strings;
5. distinguish SQL null from empty text and empty binary; and
6. terminate the file with one LF and emit no insignificant whitespace.

The schema validates representation shape. The cross-platform validator also
checks stable identifiers, hashes, report counts, safe relative paths, and
cross-document commit/run/environment bindings.

## Scenario execution

`requirements.database_version` must be `dbVersion30`. Steps are declarative:
the runner maps an action to its own DAO or Rust adapter. Scenario JSON must not
contain implementation code. A runner closes and reopens the database before
exporting a final snapshot whenever `reopen_before_snapshot` is true.

The four scenario families preserve the evidence boundary:

- `DAO-GEN-*`: DAO creates an authoritative Jet 3 fixture.
- `DAO-READ-*`: Rust reads a DAO-created file and snapshots are compared.
- `DAO-WRITE-*`: DAO opens a Rust-created file and snapshots are compared.
- `DAO-UPDATE-*`: DAO opens a Rust-updated file and both the requested change
  and unrelated semantic preservation are compared.

## Status vocabulary

Run status is `pass`, `fail`, `blocked`, or `error`. A scenario result may also
be `skipped`, but a skipped required scenario does not satisfy acceptance.

- `pass`: all declared checks ran and matched.
- `fail`: execution completed and found a semantic or expected-error mismatch.
- `blocked`: an external prerequisite was unavailable.
- `error`: the oracle or harness malfunctioned.
- `skipped`: the selected run explicitly excluded the scenario, with a reason.

No status in this protocol automatically changes the product support matrix.
That requires review of retained, clean-tree, exact-commit evidence.
