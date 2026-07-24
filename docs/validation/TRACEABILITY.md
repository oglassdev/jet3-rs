# Requirement traceability

This table identifies the minimum evidence for each requirement family.
Individual tests and DAO scenarios use stable IDs and link back to these rows.

| ID | Requirement | Acceptance gates | Required evidence |
| --- | --- | --- | --- |
| SCOPE-01 | Jet 3 / Access 97, unencrypted only | G0, G3 | version fixtures; reject/unsupported tests; DAO environment record |
| SCOPE-02 | Explicit v1 exclusions | G0, G8 | scope/matrix consistency report; negative API/CLI tests |
| CLEAN-01 | Original safe Rust; prohibited implementations not studied or used at runtime | G0, G1, G8 | provenance audit; dependency graph; consumer smoke test; unsafe-code lint |
| API-01 | Typed, streaming, lossless public API and explicit options | G1, G2 | compile/API tests; docs examples; large scan memory test; raw-value round trips |
| PHYS-01 | Header, page types, allocation and usage maps | G2, G3, G6 | golden/property/corruption tests; DAO fixture differentials |
| SCHEMA-01 | System catalog and table definitions | G2, G3, G6 | catalog/table goldens; schema snapshots; malformed tests |
| VALUE-01 | Every DAO-supported Jet 3 table column type | G2, G3 | type/boundary scenario matrix; canonical snapshots; raw-byte preservation evidence |
| ROW-01 | Null masks, fixed/variable data, code pages, dates, currency, binary, GUID/replication | G2, G3, G6 | row goldens/properties; code-page environments; DAO boundary scenarios |
| LONG-01 | Memo/OLE, multi-page values | G2, G3, G5, G6 | capacity/chain/corruption tests; fuzz corpus; DAO semantic and preservation snapshots |
| CRUD-01 | Create/drop tables and row insert/read/update/delete | G2, G3, G4 | randomized CRUD properties; DAO operation sequences; fault injection |
| INDEX-01 | Primary, unique, non-unique, composite, ascending/descending indexes | G2, G3, G5, G6 | index goldens/properties; traversal fuzzing; DAO lookup/schema snapshots |
| REL-01 | Create/drop relationships and preserve referential metadata | G2, G3 | DAO schema snapshots before/after operations and unrelated-data checks |
| DET-01 | Deterministic output under deterministic configuration | G2, G8 | byte-hash reproduction across repeated and cross-platform runs |
| TXN-01 | Copy-on-write verified publication, fsync, rename | G4 | stage-by-stage fault injection; platform filesystem tests |
| SAFE-01 | Strict bounds, structured errors, no panic/hang/unbounded allocation | G1, G2, G5, G6 | corruption matrix; resource report; fuzz and mutation results |
| VERIFY-01 | Independent verification of writer; no self-validation claim | G3, G4 | independent verifier report plus commit-bound DAO evidence |
| ORACLE-01 | Declarative DAO generator/executor/snapshot/validator | G3, G8 | versioned protocol fixtures; canonical JSON; reproducible evidence bundle |
| TEST-01 | 300 meaningful tests and 100 DAO scenarios | G2, G3 | reconciled test and scenario manifests |
| TOOL-01 | property, golden, boundary, corruption, Miri, coverage, mutation, fuzz | G2, G5, G6 | machine-readable reports and retained regressions |
| PERF-01 | required operations through 100,000 rows; 15% regression gate | G7 | Criterion data; memory/output metrics; checked baselines |
| CI-01 | Linux/macOS/Windows and required checks | G8 | commit-bound CI manifest and logs |
| RELEASE-01 | docs, examples, support matrix, reproducible fixtures | G0, G8 | release checklist and artifact manifest |

## Test and scenario identifiers

Use prefixes that preserve the evidence boundary:

- `UT-`, `IT-`, `PROP-`, `GOLD-`, `CORR-`, and `REG-` for Rust tests;
- `DAO-GEN-`, `DAO-READ-`, `DAO-WRITE-`, and `DAO-UPDATE-` for oracle
  scenarios;
- `FUZZ-`, `MUT-`, and `BENCH-` for fuzz, mutation, and benchmark records.

A manifest entry includes its traceability IDs, distinct invariant, fixture
hashes, expected result, and current execution status. Parameterization counts
as multiple cases only when the manifest explains the different invariant or
boundary exercised.
