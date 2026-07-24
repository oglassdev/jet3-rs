# Windows DAO oracle protocol

This directory defines the versioned, declarative boundary between `jet3-rs`
and a Windows-only Microsoft DAO test oracle. The oracle is an independent
fixture generator and semantic verifier. It is never a production dependency,
and this M0 protocol does not implement or describe the Jet file format.

No DAO compatibility result is checked in here yet. The example scenario is an
input document, not an executed result.

## Protocol v1

The normative protocol is in [`protocol/v1`](protocol/v1/README.md). Its JSON
Schemas describe:

- declarative scenario inputs;
- canonical semantic snapshots;
- provider and host environment records;
- per-run evidence reports; and
- immutable evidence-bundle manifests.

Every document carries `protocol_version: "1.0.0"` and a `document_type`.
Scenario IDs use `DAO-GEN-`, `DAO-READ-`, `DAO-WRITE-`, or `DAO-UPDATE-`.
Changing the semantics of a scenario requires a new content hash; changing its
meaning requires a new scenario ID.

## Provider requirement

An accepted oracle environment must activate a Microsoft DAO-capable COM
provider and successfully create, close, and delete a temporary database using
`CreateDatabase(..., dbVersion30)`. Finding a ProgID or activating COM is only
candidate discovery. It is not enough to mark the environment ready.

The probe records candidates from both 32-bit and 64-bit Windows registry
views. COM activation can only be tested in the bitness of the PowerShell
process running the probe. If a provider exists only in the other registry
view, rerun the probe with PowerShell of that bitness. Provider installation
and licensing are the operator's responsibility; this repository does not
redistribute Microsoft Access, Jet, DAO, or ACE.

Run on Windows from the repository root:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File oracle/windows-dao/scripts/probe-provider.ps1 `
  -OutputPath artifacts/dao-probe/environment.json
```

PowerShell 7 is also supported when a compatible in-process COM provider is
installed:

```powershell
pwsh -NoProfile -File oracle/windows-dao/scripts/probe-provider.ps1 `
  -OutputPath artifacts/dao-probe/environment.json
```

The probe's default behavior includes the disposable `dbVersion30` creation
test. `-SkipDbVersion30Test` exists only for inventory diagnostics and always
produces `status: "blocked"`.

Probe exit codes:

| Exit | JSON status | Meaning |
| --- | --- | --- |
| `0` | `ready` | At least one in-process provider passed `dbVersion30`. |
| `1` | `error` | The probe itself failed unexpectedly. |
| `2` | none | Invalid invocation; no trustworthy record was produced. |
| `3` | `blocked` | Non-Windows, no usable provider, bitness mismatch, or test skipped. |

`ready` is an environment result only. It is not `dao_opened` or
`dao_differential` evidence for any product capability.

## Cross-platform validation

The validator uses only Python 3's standard library:

```sh
python3 oracle/windows-dao/scripts/validate_protocol.py schemas
python3 oracle/windows-dao/scripts/validate_protocol.py document \
  oracle/windows-dao/examples/DAO-GEN-PROBE-001.scenario.json
python3 -m unittest discover -s oracle/windows-dao/tests -v
```

Validate a completed bundle and all referenced hashes:

```sh
python3 oracle/windows-dao/scripts/validate_protocol.py bundle \
  artifacts/dao-evidence/<git-commit>/<run-id>
```

The standard-library validator enforces protocol invariants and bundle hashes.
The JSON Schemas remain the portable machine-readable contract and may also be
used with any Draft 2020-12 validator.

## Evidence layout

A completed run is immutable and has this layout:

```text
artifacts/dao-evidence/<40-hex-git-commit>/<run-id>/
  bundle-manifest.json
  environment.json
  report.json
  scenarios/<scenario-id>/input.json
  scenarios/<scenario-id>/dao-snapshot.json
  scenarios/<scenario-id>/rust-snapshot.json
  scenarios/<scenario-id>/operation-log.json
  databases/<content-sha256>.mdb
```

The manifest records the SHA-256 and role of every retained payload file; it
does not self-hash `bundle-manifest.json`. The report records the exact clean
git commit, oracle revision, command line, timestamps, environment record hash,
and explicit pass/fail/blocked/skipped outcome for every selected scenario. A
dirty worktree may be used diagnostically but cannot produce
release-satisfying evidence. Reports from another commit are stale.

## Current host limitation

The workspace host used to implement and cross-platform-test M0 is macOS on
ARM64, with neither `powershell.exe` nor `pwsh` available in `PATH`. It can
validate protocol documents and evidence hashes, but it cannot execute the
Windows COM oracle. A real release run requires a provisioned Windows host and
a provider that passes the `dbVersion30` test. Until a Windows run records that
fact, provider availability is `blocked`, not assumed.
