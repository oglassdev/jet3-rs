# Write differential

`write-scenarios.json` is a separate bounded inventory with operation mode
`dao_open_rust`. It does not replace or relax the protocol 1.2 read inventory.
Snapshots retain the existing canonical shape; write coverage receipts are
validated against the write inventory by `dao_write_diff.py`.

The Ubuntu job creates each recipe with one public creation call, validates a
Rust snapshot against the recipe, and uploads the exact files. Publication is
currently Unix-only. The Windows 2022 job downloads and verifies those files,
produces a read-only Rust snapshot, probes the stock x86 DAO provider, and opens
the same files read-only through DAO. It compares the complete protocol
snapshots and independently checks the requested schema, typed rows and
relationships. Every index also gets complete DAO traversal and full-key Seek;
ordinary duplicate-key Seek may select any matching complete row.

Local generation without DAO:

```sh
cargo build -p jet3-cli -p jet3-testkit --bin jet3-cli --bin jet3-write-fixture
python3 oracle/windows-dao/scripts/dao_write_diff.py prepare OUT target/debug/jet3-write-fixture target/debug/jet3-cli SOURCE_REVISION
```

The workflow's acquisition gate requires the later reviewed EXP-0141 plan and
its SHA-256. This scaffold does not contain that finalized plan and cannot run
acquisition yet. EXP-0142 is reserved for the outcome. Probe and failure
artifacts are retained; no runtime installer, automatic retry or support-matrix
promotion is included. Inventory deferrals describe the remaining bounds.
