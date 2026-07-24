# Validation document schemas

These JSON Schema Draft 2020-12 documents define the machine-readable shapes
used by the validation contract. They are documentation contracts: the project
validator implements the support-matrix rules without requiring a third-party
JSON Schema package.

- `support-matrix.schema.json` describes the capability ledger.
- `test-manifest.schema.json` describes deterministic Rust test cases.
- `fixture-manifest.schema.json` describes reproducible fixture origins.
- `benchmark-manifest.schema.json` describes checked benchmark baselines.
- `gate-result.schema.json` describes one acceptance-gate result.

Repository paths in these documents use forward slashes, are relative to the
repository root, and must not contain `.` or `..` path components. SHA-256
values are lowercase hexadecimal.

Run the currently enforced validation with:

```sh
python3 tools/validate_contract.py
python3 tools/validate_contract.py --self-test
```
