# Windows DAO oracle protocol

This directory defines the versioned, declarative boundary between `jet3-rs`
and a Windows-only Microsoft DAO test oracle. The oracle is an independent
fixture generator and semantic verifier. It is never a production dependency,
and this M0 protocol does not implement or describe the Jet file format.

No DAO compatibility result is checked in here yet. The examples are input
documents, not executed results.

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

## Protocol 1.1 M1 plans

[`protocol/v1_1`](protocol/v1_1/README.md) adds a separate, fail-closed M1 data
contract for controlled DAO-generated experiments. It covers independent empty
creation repeats, a fixed `dbBinary` marker row, a `dbText(8)` nonunique-index
pair, and `dbMemo`/`dbLongBinary` boundary ladders at lengths 1, 2047, 2048,
2049, 32767, 32768, and 32769. The contract includes an exact deep semantic
snapshot comparator, multi-scenario/pair reports, immutable bundle bindings,
and a hash-checked example inventory.

M1 now has a reviewed COM executor and atomic protocol-1.1 publisher. The
executor binds the complete inventory, exact clean commit, x86 host/provider
registration, and locked provider binary before COM or output mutation. It
uses the `System.Byte[]` representations established by `EXP-0006`, reopens
every project-generated database through DAO, records structured runtime and
error observations, validates the complete staged bundle, and publishes it
with one same-volume collision-refusing directory move. The complete inventory
passed from clean commit `c2e5df29bcd5a779d6aa82582513e28b53f76598`;
the retained manifest hash is
`9bc59d5db419e7283d8013d34e4fea16c3a9add8830c392294b8a8b6b1c32685`.
That is DAO-only controlled generation/readback evidence, not Rust or general
compatibility evidence. The older non-executing preflight remains an explicit
diagnostic command. Protocol 1.0 and its checked M0 runner remain unchanged.

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

## GitHub-hosted campaign lane

Actions run
[`32327232241`](https://github.com/oglassdev/jet3-rs/actions/runs/32327232241)
at exact commit `8300196ae8c72b45b8d0af87567ab549fea29567` established
that both probed stock images already contained an x86 provider capable of the
disposable `dbVersion30` test. The conditional Microsoft 365 Access Runtime
installation and post-install probe were therefore skipped in both jobs. See
`EXP-0036`.

The reviewed campaign lane is `windows-2022`. On image
`20260802.262.1`, x86 Windows PowerShell activated machine-registered
`DAO.DBEngine.36` from
`C:\Program Files (x86)\Common Files\Microsoft Shared\DAO\dao360.dll`.
The provider reported version 3.6; the file version was `03.60.9765.0` and its
SHA-256 was
`4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`.
That exact identity matches the historically reviewed local provider.

The `windows-2025` diagnostic lane also passed from the untouched image, but
its patched `dao360.dll` was file version `10.0.26100.5074` with SHA-256
`c2da31acb8836c976c22843862eec36114d4fd3c42e8642190f4c4629273ad3e`.
It is not the pinned campaign lane. GitHub image contents can change, so every
campaign run must repeat the probe and bind the exact image, runtime,
registration, provider path, version, and hash before acquisition. A mismatch
blocks the run; it is not repaired by silently selecting the other image or by
installing a different provider.

This result proves hosted provider capability only. It is not an A1
acquisition, an MDB-format observation, Rust validation, or DAO compatibility
evidence.

The same pinned lane subsequently completed the full A1 workflow in Actions
run [`32486063559`](https://github.com/oglassdev/jet3-rs/actions/runs/32486063559)
from exact commit `947038265f6898c55b39da99340220e548836594`; see `EXP-0039`.

## A1 allocation-map campaign status

The checked A1 materials under [`experiments/a1`](experiments/a1/README.md)
define `DAO-A1-ALLOCATION-MAPS-001`, a three-replica, preregistered DAO-only
physical experiment. The acquisition controller, independent bundle validator,
bounded analysis, and corruption tests completed end to end in run
`32486063559`. The status was `independently_validated`, and the retained
manifest SHA-256 is
`97c1286624a5e02fc7bcfc7b1047986e8a15e3ac8aec22488a1a5b4bfa444381`.
The preregistered analysis returned `no_scientific_outcome` with sole reason
`ambiguous_record_boundary`; it examined zero candidate models and did not
evaluate the holdout.

Execution must use a clean, exact pushed commit on the pinned `windows-2022`
lane. Before any database is created, the x86 protocol-1.1 provider probe must
again return `ready` for `DAO.DBEngine.36`, and the provider binary must match
the reviewed file version `03.60.9765.0` and SHA-256
`4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`.
Provider or image drift blocks the run; it must not be repaired by silently
using `windows-2025` or installing a different provider.

The controlled entry point is invoked only through x86 Windows PowerShell 5:

```powershell
$commit = git rev-parse HEAD
$runId = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ") + "-a1-hosted"
$work = Join-Path $env:RUNNER_TEMP "jet3-rs-a1"
$environment = Join-Path $work "environment.json"
$output = Join-Path $work "evidence"
$winps32 = "$env:WINDIR\SysWOW64\WindowsPowerShell\v1.0\powershell.exe"

& $winps32 -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass `
  -File oracle/windows-dao/scripts/probe-provider.ps1 `
  -ProtocolVersion 1.1.0 `
  -OutputPath $environment
if ($LASTEXITCODE -ne 0) { throw "The A1 provider probe did not pass." }

& $winps32 -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass `
  -File oracle/windows-dao/scripts/run-a1-controlled.ps1 `
  -RepositoryRoot (Get-Location) `
  -EnvironmentPath $environment `
  -OutputRoot $output `
  -GitCommit $commit `
  -RunId $runId
if ($LASTEXITCODE -ne 0) { throw "The controlled A1 campaign did not pass." }

$bundle = Join-Path (Join-Path $output $commit) $runId
python oracle/windows-dao/scripts/a1_contract.py validate-bundle $bundle
if ($LASTEXITCODE -ne 0) { throw "Independent A1 bundle validation failed." }
```

Run `32486063559` performed those same gates, retained the complete validated
bundle, and installed no provider software. Acquisition has now started under
the `EXP-0038` retained-replica-observation criterion, so any analyzer change
requires a new experiment ID, plan, and provenance entry. The no-outcome bundle
assigns no physical-format meaning and does not establish general allocation-map
behavior, Rust correctness, a support-matrix advance, or DAO compatibility.

For an M1 preflight, request a protocol 1.1 environment record explicitly:

```powershell
$m1Work = Join-Path $env:TEMP "jet3-rs-dao-m1"
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File oracle/windows-dao/scripts/probe-provider.ps1 `
  -ProtocolVersion 1.1.0 `
  -OutputPath (Join-Path $m1Work "environment-m1.json")
```

## Run the checked M0 DAO scenario

The M0 runner supports exactly
`examples/DAO-GEN-PROBE-001.scenario.json`. It refuses a dirty worktree, a
commit mismatch, a reused run ID, a non-ready or different-host environment,
provider drift, and unsupported scenario actions. Keep generated probe and
evidence output outside the git worktree so the clean-tree check remains
meaningful:

```powershell
$commit = git rev-parse HEAD
$runId = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ") + "-dao-m0"
$work = Join-Path $env:TEMP "jet3-rs-dao"

powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File oracle/windows-dao/scripts/probe-provider.ps1 `
  -OutputPath (Join-Path $work "environment.json")

powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File oracle/windows-dao/scripts/run-dao-gen-probe.ps1 `
  -RepositoryRoot (Get-Location) `
  -EnvironmentPath (Join-Path $work "environment.json") `
  -OutputRoot (Join-Path $work "evidence") `
  -GitCommit $commit `
  -RunId $runId
```

The runner late-binds only the exact provider accepted by the probe, verifies
the provider binary hash and process bitness, calls DAO
`CreateDatabase(..., dbVersion30)`, closes and reopens the file, and confirms
through DAO that no user table exists. It then publishes the bundle by a
same-volume directory move. A partial staging directory is retained if
publication itself fails.

Runner exit codes:

| Exit | Report status | Meaning |
| --- | --- | --- |
| `0` | `pass` | The checked scenario ran and a complete bundle was published. |
| `1` | `fail` | DAO ran but did not satisfy the scenario. |
| `2` | none | Invocation or checked scenario input was invalid. |
| `3` | `blocked`, or no bundle during preflight | Required host/provider/clean-commit state was unavailable. |
| `4` | `error`, or retained staging during publication | The runner or publication failed unexpectedly. |

A passing M0 bundle proves only the recorded empty-database scenario on its
exact clean commit and environment. It is not a general reader, writer, CRUD,
or differential compatibility claim.

## Run the non-executing M1 preflight

The M1 preflight deliberately selects the complete controlled 1.1 inventory;
it does not permit a partial suite that avoids the unresolved binary cases. It
verifies the exact clean commit and checked examples, then binds a ready
protocol 1.1 environment to the same Windows host, process architecture,
provider registration record, and provider binary hash:

```powershell
$commit = git rev-parse HEAD
$runId = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ") + "-dao-m1"
$m1Work = Join-Path $env:TEMP "jet3-rs-dao-m1"

powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File oracle/windows-dao/scripts/preflight-m1-controlled.ps1 `
  -RepositoryRoot (Get-Location) `
  -EnvironmentPath (Join-Path $m1Work "environment-m1.json") `
  -OutputRoot (Join-Path $m1Work "evidence") `
  -GitCommit $commit `
  -RunId $runId
```

Even after every precondition succeeds, this command exits `3` with
`BLOCKED`. It performs no COM activation, creates no database or directory,
and publishes no evidence bundle. Python 3 is required so the same checked
standard-library validator can reject malformed inventory and environment
documents before any provider precondition is accepted. This diagnostic
preserves the historical pre-executor boundary. `EXP-0006` subsequently
established the late-bound runtime types and failure behavior; the separate
controlled executor below consumes that result.

## Run the controlled M1 executor

The executor accepts only the checked seven-scenario/two-pair inventory and a
ready protocol-1.1 environment record. Use 32-bit Windows PowerShell for the
recorded x86 provider and keep output outside the repository:

```powershell
$commit = git rev-parse HEAD
$runId = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ") + "-dao-m1"
$m1Work = Join-Path $env:TEMP "jet3-rs-dao-m1"
$winps32 = "$env:WINDIR\SysWOW64\WindowsPowerShell\v1.0\powershell.exe"

& $winps32 -NoProfile -ExecutionPolicy Bypass `
  -File oracle/windows-dao/scripts/run-m1-controlled.ps1 `
  -RepositoryRoot (Get-Location) `
  -EnvironmentPath (Join-Path $m1Work "environment-m1.json") `
  -OutputRoot (Join-Path $m1Work "evidence") `
  -GitCommit $commit `
  -RunId $runId
```

Every input is size-bounded before parsing. The environment and provider stay
open through locked read handles; Git and provider identity are rechecked
immediately before COM and publication. All retained files receive
`FileStream.Flush(true)` before hashing and publication. Managed .NET exposes
no safe parent-directory fsync primitive on Windows, so the publisher claims
atomic visibility from the same-volume directory rename, not guaranteed
power-loss persistence of the parent-directory entry.

Exit codes retain the M0 meanings: `0` pass, `1` controlled fail, `2` invalid
invocation, `3` blocked precondition, and `4` executor/publication error. DAO
scenario failures are preserved in an atomically published non-passing bundle;
publication failures expose no final bundle.

## Run the allowlisted jobs over Tailscale and Windows OpenSSH

[`scripts/windows-dao-ssh.py`](../../scripts/windows-dao-ssh.py) automates the
provider probe and controlled M1 executor on a private Windows host. It is an
OpenSSH client, not a general remote-command runner. The remote account can run
only `provider-probe` or `m1-controlled` through this interface.

Set up the Windows host once:

1. Install Tailscale, sign in, enable unattended operation, and restrict TCP 22
   to the operator device or identity in the tailnet policy. Do not forward
   port 22 from the public internet.
2. Install the Windows OpenSSH Server capability, start `sshd`, and configure
   the service to start automatically.
3. Create a dedicated standard account such as `jet3runner` and put only the
   automation public key in that account's `authorized_keys` file. Keep the
   private key outside this repository.
4. Scope the Windows firewall SSH rule to the Tailscale interface or tailnet
   addresses.
5. Record the server's Ed25519 host-key fingerprint on Windows with
   `ssh-keygen -lf C:\ProgramData\ssh\ssh_host_ed25519_key.pub`. On the client,
   inspect the key returned by `ssh-keyscan`, compare that fingerprint through
   a separate trusted channel, and only then add the key to `known_hosts`.

The client sets `BatchMode=yes` and `StrictHostKeyChecking=yes`; it will neither
prompt for a password nor trust a new host key automatically. Verify a normal
key-only login before running a DAO job:

```sh
ssh -i ~/.ssh/jet3-dao jet3runner@dao-host.tailnet-name.ts.net
```

Run the x86 provider probe from the repository root:

```sh
./scripts/windows-dao-ssh.py provider-probe \
  --host dao-host.tailnet-name.ts.net \
  --user jet3runner \
  --identity ~/.ssh/jet3-dao
```

Run the complete controlled M1 job:

```sh
./scripts/windows-dao-ssh.py m1-controlled \
  --host dao-host.tailnet-name.ts.net \
  --user jet3runner \
  --identity ~/.ssh/jet3-dao \
  --output artifacts/windows-dao-ssh/m1.zip
```

By default, remote runs use
`C:\Users\<ssh-user>\AppData\Local\jet3-rs-ssh`. If the dedicated account has
a nonstandard profile location, pass an explicit absolute local-drive path with
`--remote-root`.

Use `--dry-run` to inspect the fixed SSH/SCP command plan without contacting
Windows. Dry-run still enforces the local source binding: `HEAD` must be
advertised by a ref on the selected Git remote, with no tracked changes and no
untracked files outside `artifacts/`. Repository clone URLs are limited to
credential-free HTTPS. The Windows entrypoint creates a fresh exclusive run
directory, checks out that exact commit detached, requires a clean checkout,
and verifies client-computed SHA-256 values for the uploaded entrypoint and
process module before invoking either file. It also verifies that both files
hash-match their copies in the detached checkout before running a job.

Both jobs invoke the probe through x86 Windows PowerShell. `m1-controlled`
runs the same probe first and starts M1 only when the environment is ready.
Each child has a reviewed 120-second maximum and 1-MiB output maximum;
bootstrap failures terminate the complete Git process tree. The
artifact tree and ZIP have a configurable byte ceiling. A ZIP is downloadable
only for pass (`0`), controlled fail (`1`), or blocked (`3`); invalid or
unexpected errors do not produce a downloadable archive. The client verifies
the complete resolved run-root path and SHA-256, monitors the local file during
transfer, and terminates SCP as soon as it exceeds the declared byte length.
Before reporting success, it also performs bounded structural validation of the
ZIP inventory and its request-identity JSON without extracting the archive.
Remote run directories are intentionally retained as audit material and are
never reused.

## Observe controlled M1 physical page differences

The bounded M2 observer accepts only the complete passing M1 bundle after its
protocol validator and exact manifest hash succeed. It requires the observer
repository at an exact clean commit, reads no external or donated MDB, and
publishes one collision-refusing JSON document outside the repository:

```powershell
$commit = git rev-parse HEAD
$bundle = Join-Path $env:TEMP "jet3-rs-dao-m1-executor\evidence\$m1Commit\$m1RunId"
$output = Join-Path $env:TEMP "jet3-rs-m2-observation\$commit\m1-pages.json"

python oracle/windows-dao/scripts/observe_m1_pages.py `
  --bundle $bundle `
  --manifest-sha256 $m1ManifestSha256 `
  --repository-root (Get-Location) `
  --git-commit $commit `
  --output $output
```

The output records only ordered 2-KiB page hashes and bounded page/byte
difference counts for the two controlled semantic pairs. It deliberately
asserts no field offsets, page classes, row layout, storage structure, or
compatibility. The M1 source bundle remains the independent Microsoft DAO
evidence; this observer is a reproducible descriptive analysis of those
retained files. Publication fsyncs a private same-directory staging file and
uses a collision-refusing hard-link commit; as with the M1 publisher, Windows
offers no safe parent-directory fsync claim here.

## Cross-platform validation

The validator uses only Python 3's standard library:

```sh
python3 oracle/windows-dao/scripts/validate_protocol.py schemas
python3 oracle/windows-dao/scripts/validate_protocol.py document \
  oracle/windows-dao/examples/DAO-GEN-PROBE-001.scenario.json
python3 -m unittest discover -s oracle/windows-dao/tests -v
```

Check the separate M1 contract and all inventoried examples:

```sh
python3 oracle/windows-dao/scripts/build_m1_examples.py --check
python3 oracle/windows-dao/scripts/validate_m1_protocol.py schemas
python3 oracle/windows-dao/scripts/validate_m1_protocol.py document \
  oracle/windows-dao/examples/m1-inventory.json
```

Validate a completed bundle and all referenced hashes:

```sh
# Protocol 1.0 / M0
python3 oracle/windows-dao/scripts/validate_protocol.py bundle \
  artifacts/dao-evidence/<git-commit>/<run-id>

# Protocol 1.1 / M1
python3 oracle/windows-dao/scripts/validate_m1_protocol.py bundle \
  artifacts/dao-evidence/<git-commit>/<run-id>
```

The standard-library validators share one version-neutral schema engine and
common snapshot, environment, and operation-log checks; v1 and v1.1 retain
separate semantic adapters. The engine explicitly inventories its supported
Draft 2020-12 keywords, rejects schemas that use any other keyword, and
enforces all supported constraints in one recursive pass. It also enforces
protocol invariants and bundle hashes. The JSON Schemas remain the portable
machine-readable contract and may also be used with any Draft 2020-12
validator.

## Evidence layout

A completed run is immutable and has this layout:

```text
artifacts/dao-evidence/<40-hex-git-commit>/<run-id>/
  bundle-manifest.json
  environment.json
  inventory.json                         # M1
  report.json
  scenarios/<scenario-id>/input.json
  scenarios/<scenario-id>/dao-snapshot.json
  scenarios/<scenario-id>/operation-log.json
  pairs/<pair-id>/input.json             # M1
  databases/<content-sha256>.mdb
```

The manifest records the SHA-256 and role of every retained payload file; it
does not self-hash `bundle-manifest.json`. The report records the exact clean
git commit, oracle revision, command line, timestamps, environment record hash,
and explicit pass/fail/blocked/skipped outcome for every selected scenario. A
dirty worktree may be used diagnostically but cannot produce
release-satisfying evidence. Reports from another commit are stale.

## Current host status

This workspace is on x64 Windows with both 64-bit and 32-bit Windows
PowerShell. The retained protocol-1.1 environment identifies an x86
`DAO.DBEngine.36` provider that passed disposable `dbVersion30` creation.
Provider availability alone was not M1 evidence. The complete executor has now
published the independently validated bundle recorded by `EXP-0007`. Rust
capabilities remain unverified until a commit-bound differential scenario
compares DAO and Rust canonical results.
