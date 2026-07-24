# Validation document schemas

These JSON Schema Draft 2020-12 documents define the machine-readable shapes
used by the validation contract. They are documentation contracts: the project
validator implements the support-matrix rules without requiring a third-party
JSON Schema package.

- `support-matrix.schema.json` describes the capability ledger.
- `test-manifest.schema.json` describes deterministic Rust test cases.
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

Repository paths in these documents use forward slashes, are relative to the
repository root, and must not contain `.` or `..` path components. SHA-256
values are lowercase hexadecimal.

Run the currently enforced validation with:

```sh
python3 tools/validate_contract.py
python3 tools/validate_contract.py --self-test
```
