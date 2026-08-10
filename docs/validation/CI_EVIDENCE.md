# Cross-platform G1 CI evidence

G1 portability evidence is produced only by the Linux, macOS, and Windows
matrix jobs in `.github/workflows/ci.yml`. Each job starts from the checked-out
commit and records its dirty state before creating artifacts. It then runs the
fixed G1 command inventory on Rust 1.96.0:

- formatting, Clippy, workspace tests, and public documentation with warnings
  denied;
- the 800-line production-source limit;
- fail-closed reconciliation of the checked test inventory with Cargo's
  runtime test listing; and
- reconciliation of checked `SAFE-01` malformed-input coverage in
  `tests/manifest.json`.

The platform record contains the full commit, platform, exact Rust release and
host data, fixed command arguments, exit codes, log paths, and SHA-256 hashes.
It intentionally contains no timestamps or mutable run labels. This makes its
JSON serialization deterministic while retaining the original command logs.

The aggregation job downloads only artifacts whose names contain the current
GitHub commit. `tools/ci_evidence.py aggregate` nevertheless validates their
contents instead of trusting artifact names. It rejects stale commits, dirty
runs, failed or drifted commands, incomplete inventories, altered logs,
duplicate platforms, and a missing Linux, macOS, or Windows record. The
declared platform must also match the pinned compiler's host triple. The
resulting `g1-cross-platform-<40-character-commit>` artifact contains
`aggregate.json`, all three platform records, and their logs.

This is Rust quality and portability evidence only. It is not DAO evidence and
does not establish Microsoft Access compatibility.

## Selecting downloaded evidence for full acceptance

Download and extract the aggregate artifact for the exact commit being tested.
Select it explicitly; the acceptance runner never searches a cache or silently
chooses the newest artifact:

```sh
export JET3_G1_EVIDENCE=/absolute/path/to/g1-cross-platform-COMMIT
./scripts/acceptance.sh full
```

`JET3_G1_EVIDENCE` may name the extracted directory or its `aggregate.json`.
G1 recomputes `git rev-parse HEAD` and verifies the aggregate, platform-record,
and log hashes against that commit. A stale, partial, duplicate, dirty, failed,
or modified bundle fails G1. If the variable is absent, G1 remains `BLOCKED`;
ordinary local quick checks do not require a download or network access and
cannot substitute for cross-platform evidence.

For a focused inspection:

```sh
python3 tools/ci_evidence.py verify-aggregate \
  "$JET3_G1_EVIDENCE" \
  --expected-commit "$(git rev-parse HEAD)"
```
