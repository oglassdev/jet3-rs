# Validation document schemas

These JSON Schema Draft 2020-12 documents define the machine-readable shapes
used by the validation contract. They are documentation contracts: the project
validator implements the support-matrix rules without requiring a third-party
JSON Schema package.

- `support-matrix.schema.json` describes the capability ledger.
- `test-manifest.schema.json` describes deterministic Rust test cases.
  A case may use `platforms` only as one of the sorted nonempty subsets
  `["unix"]`, `["windows"]`, or `["unix", "windows"]`; omission means both.
- `test-observation.schema.json` describes a commit-bound Cargo inventory
  observation (discovery and ignored state only). It is not pass/fail execution
  evidence; executed test results remain a separate acceptance-gate report.
- `fixture-manifest.schema.json` describes reproducible fixture origins.
- `external-corpus.schema.json` describes the opt-in, read-only external corpus.
- `benchmark-manifest.schema.json` describes checked benchmark baselines.
- `gate-result.schema.json` describes one acceptance-gate result.
- `acceptance-manifest.schema.json` describes the files retained by one run.
- `acceptance-summary.schema.json` describes deterministic run counts and hashes.
- `traceability-registry.schema.json` describes the authoritative requirement-ID registry.
- `ci-platform-evidence.schema.json` describes one deterministic, commit-bound
  Linux, macOS, or Windows G1 command record and its hashed logs.
- `ci-aggregate-evidence.schema.json` describes the exact-commit three-platform
  aggregate consumed by G1. The validator also enforces uniqueness, canonical
  command order and arguments, compiler-host/platform agreement, and
  referenced-file hashes.
- `g6-core-inventory.schema.json`, `g6-coverage-evidence.schema.json`, and
  `g6-mutation-evidence.schema.json` describe the commit-bound G6 inputs.
- `g6-cargo-mutants-outcomes-v26.schema.json` records the one accepted native
  mutation-producer shape; `g6-mutation-report.schema.json` describes its
  losslessly reconciled normalized records and review annotations.
- `repository-contract.schema.json` describes the fail-closed G0 inventory of
  workspace roles, permitted runtime packages, format-knowledge files, and the
  three distinct fixture classes.

The repository fixture manifest may be empty only when the checked
`fixtures/generated`, `fixtures/malformed`, and `fixtures/regression`
directories contain no fixture files. Synthetic fuzz seeds are independently
complete and hash-bound by `fuzz/corpus/manifest.json`. The donated external
corpus is observational, nonredistributable, nonregenerable, optional, and
never release evidence; its identity and handling records remain in
`external-corpus.json`, `EXTERNAL_CORPUS.md`, and `docs/PROVENANCE.md`.

Every Rust source file below each production package's `src` directory is
inventoried by exact SHA-256. A format-assertion file names one or more existing
provenance IDs, and every named ID must also appear in that source file.
Reviewed test-only, presentation, or explicit non-assertion files instead
record why they contain no production physical-format knowledge. Any source
change or newly discovered source file fails G0 until that classification and
its provenance are reviewed. Production packages may not use a build script or
introduce a Cargo `custom-build` target.

Repository paths in these documents use forward slashes, are relative to the
repository root, and must not contain `.` or `..` path components. SHA-256
values are lowercase hexadecimal.

Run the currently enforced validation with:

```sh
python3 tools/validate_contract.py
python3 tools/validate_contract.py --self-test
```
