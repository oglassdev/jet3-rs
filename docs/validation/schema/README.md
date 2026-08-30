# Validation document schemas

These JSON Schema Draft 2020-12 documents define the machine-readable shapes
used by the validation contract. They are documentation contracts: the project
validator implements the support-matrix rules without requiring a third-party
JSON Schema package.

- `support-matrix.schema.json` describes the capability ledger. Its closed,
  ordered capability list fixes the complete v1 ID inventory.
- `fixture-manifest.schema.json` describes reproducible fixture origins.
- `external-corpus.schema.json` describes the opt-in, read-only external corpus.
- `benchmark-manifest.schema.json` describes checked benchmark baselines.

The repository fixture manifest may be empty only when the checked
`fixtures/generated`, `fixtures/malformed`, and `fixtures/regression`
directories contain no fixture files. Synthetic fuzz seeds are independently
complete and hash-bound by `fuzz/corpus/manifest.json`. The donated external
corpus is observational, nonredistributable, nonregenerable, optional, and
never release evidence; its identity and handling records remain in
`external-corpus.json`, `EXTERNAL_CORPUS.md`, and `docs/PROVENANCE.md`.

Repository paths in these documents use forward slashes, are relative to the
repository root, and must not contain `.` or `..` path components. SHA-256
values are lowercase hexadecimal.

Run the currently enforced validation with:

```sh
python3 tools/validate_contract.py
```
