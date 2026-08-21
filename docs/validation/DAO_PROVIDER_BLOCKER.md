# DAO provider and differential-evidence boundary

Status: **BLOCKED — hosted provider capability confirmed; release differential evidence absent**

Audit date: 2026-08-20

This record separates a historically observed provider capability, retained
controlled evidence, and the evidence required on an exact release commit. A
local Windows host demonstrated a usable x86 Microsoft DAO provider during the
historical runs below. Actions run `32327232241` subsequently demonstrated a
matching stock provider on `windows-2022` and a distinct patched provider on
`windows-2025`; see `EXP-0036`. M0 and M1 completed historically, M2 analyzed
retained M1 output descriptively, and M3 completed a replicated one-variable
physical-delta campaign. None of those earlier-commit or provider-only results
satisfies G3 for a later release commit.

## Exact current blocker

Provider discovery is no longer blocking current implementation or controlled
A1 acquisition: `windows-2022` is the pinned campaign lane, subject to an exact
fresh probe and all A1 execution gates. G3 remains blocked because the project
lacks both:

1. the required Rust implementation and 100-scenario DAO-versus-Rust
   differential inventory; and
2. complete evidence generated from the exact clean release commit.

The support-matrix validator also intentionally rejects `dao_bundle` evidence
until semantic bundle integration is implemented. DAO-only generation,
readback, or physical observation cannot advance an untested Rust capability.

## Historical controlled evidence

These records remain useful, immutable historical observations:

- Corrected M0: clean commit
  `416b834b0d786fdf68efa066ab0e38409e443edf`, one passing
  `DAO-GEN-PROBE-001` bundle retained at
  `%TEMP%\jet3-rs-dao-m0\evidence\416b834b0d786fdf68efa066ab0e38409e443edf\20260724T234905Z-dao-m0`,
  manifest SHA-256
  `4651e07957e1740c07c735ac74f2c1e6e7c9038ae9d9bb362b78860453c4c326`.
  The checked protocol-1.0 validator independently accepted this retained
  bundle on 2026-07-25. See `EXP-0009`.
- M1: clean commit
  `c2e5df29bcd5a779d6aa82582513e28b53f76598`, all seven controlled
  scenarios and both semantic pairs passed. See `EXP-0007` and
  `M1_DAO_EVIDENCE.md`.
- M2: clean observer commit
  `550ddc266eddf7e6765cf929ef50fd5aac19c542`, bounded descriptive page and
  byte observations over the retained M1 bundle. See `EXP-0008` and
  `M2_PAGE_OBSERVATION.md`.
- M3: clean producer commit
  `9977745e6515363cbb179d8d949d34604554b2cd`, nine fresh-process DAO
  samples and 18 bounded descriptive comparisons retained at
  `%TEMP%\jet3-rs-dao-m3\evidence\9977745e6515363cbb179d8d949d34604554b2cd\20260725T024333Z-dao-m3`,
  manifest SHA-256
  `15a7abb3b768ea94233dc3d525a069fb25e595b0ed649f063d117697a6e3c55e`.
  See `EXP-0010` and `M3_REPLICATED_DELTA_EVIDENCE.md`.

The phrase “historical evidence” is deliberate: current HEAD is different
from each producer commit. These artifacts cannot be relabeled as current
release evidence.

## Local provider identity

Windows 11 Pro build 22631 has an x86 `DAO.DBEngine.36` provider whose
`dao360.dll` file version is 03.60.9765.0 and whose SHA-256 is
`4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`.
The checked x86 probes passed disposable `CreateDatabase(..., dbVersion30)`
for protocol 1.0 and 1.1 records. x64 Windows PowerShell and PowerShell 7
cannot activate this x86-only provider.

The retained protocol-1.1 environment record used by M1 has SHA-256
`870ec9ceaaa6a5b9af0ebf16fbf0ef793b943718b49d9f003ed48cfd65af679f`.
Every new DAO execution must revalidate the process architecture, COM
registration, provider path and hash, host/runtime identity, and exact clean
Git commit before COM activation or output mutation.

## Oracle implementation boundary

Protocol 1.0 implements only the M0 empty-database operation. Protocol 1.1
adds the controlled seven-scenario M1 DAO generator/readback executor, deep
semantic pair comparison, fail-closed validator, and private same-volume
atomic publisher. The retained marshalling experiment established direct
`System.Byte[]` assignment for `dbBinary` and
`dbLongBinary.AppendChunk(System.Byte[])` on this exact x86 environment.

M2 is not another DAO executor. It is a bounded observer over immutable M1
files and assigns no physical format meaning. M3 created repeated fresh
controlled samples to distinguish stable physical deltas from run-specific
variance. Its results remain DAO-only descriptive evidence and cannot satisfy
G3 or advance the support matrix.

The protocol-1.0 differential modes `rust_read_dao`, `dao_open_rust`, and
`dao_verify_rust_update` remain rejected until their Rust canonical semantic
comparison and preservation checks are implemented.

## Hosted runners and provisioning

The authoritative checked probe in Actions run
[`32327232241`](https://github.com/oglassdev/jet3-rs/actions/runs/32327232241)
ran from exact commit `8300196ae8c72b45b8d0af87567ab549fea29567` on 2026-08-20.
The untouched `windows-2022` image `20260802.262.1` activated the same reviewed
x86 `DAO.DBEngine.36` identity as the local host: `dao360.dll` file version
`03.60.9765.0`, SHA-256
`4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`.
It passed disposable `CreateDatabase(..., dbVersion30)` and is the pinned
campaign lane.

The untouched `windows-2025` image `20260810.198.2` also passed, but with a
patched `dao360.dll` file version `10.0.26100.5074` and distinct SHA-256
`c2da31acb8836c976c22843862eec36114d4fd3c42e8642190f4c4629273ad3e`.
It remains a diagnostic lane rather than an interchangeable campaign host.
Because both stock probes returned ready, the conditional Microsoft 365 Access
Runtime installation and post-install probes were skipped. No runtime-installed
provider contributed to this observation.

Hosted images are mutable. Every future acquisition must bind the exact clean
producer commit and revalidate image identity, x86 process architecture, COM
registration, provider path, file version and hash, plus disposable
`dbVersion30` creation. Any drift blocks execution pending review. This
provider-capability result is not A1 output, a physical-format result, Rust
verification, or a compatibility claim. The project still does not install or
redistribute Access, DAO, or ACE without an operator licensing decision.

## A1 acquisition boundary

`DAO-A1-ALLOCATION-MAPS-001` has checked acquisition, bundle-validation, and
bounded-analysis code, but it has not been executed. The earlier hosted run did
not create an A1 database, inspect campaign pages, or publish an A1 bundle.
Consequently there is no A1 scientific outcome, allocation-map observation,
Rust verification result, or new compatibility evidence to cite.

The first acquisition remains blocked until one exact pushed commit contains
the frozen preregistration and a manual hosted workflow pinned to
`windows-2022`. That workflow must repeat the stock x86 provider probe, reject
any image or provider-identity drift, run the controlled entry point, validate
the complete retained bundle independently, and upload that exact bundle. It
must not fall back to `windows-2025` or install a replacement provider. Even a
passing campaign would remain descriptive DAO-only physical evidence and would
not satisfy the release differential gate.

## Requirements for future release evidence

For an exact release commit:

1. require a clean checkout and bind every checked input and executed source;
2. probe and retain the exact provider/environment identity;
3. execute the complete declarative DAO-versus-Rust scenario inventory;
4. retain canonical DAO and Rust snapshots plus preservation results;
5. atomically publish and independently validate the immutable bundle; and
6. reference that exact-commit bundle from the support matrix and acceptance
   record.

Until every applicable step exists and passes, G3 and release claims depending
on DAO remain `BLOCKED`.

On 2026-08-20, A1 dispatch run
[`32437968174`](https://github.com/oglassdev/jet3-rs/actions/runs/32437968174)
failed closed when `windows-2022` image identity drifted, demonstrating that
the acquisition guard rejects an unproved hosted image before DAO execution.
GitHub was concurrently serving image versions `20260802.262.1` and
`20260818.277.1`. Re-proof run
[`32438973969`](https://github.com/oglassdev/jet3-rs/actions/runs/32438973969),
retained as artifact
`windows-dao-hosted-windows-2022-88b2118dbcef621e7b8bf56c5d1ae623e7d1b49f-1`,
proved `20260802.262.1`; re-proof run
[`32439805418`](https://github.com/oglassdev/jet3-rs/actions/runs/32439805418),
retained as artifact
`windows-dao-hosted-windows-2022-c3bcd5683f864074776b7d218d53c410aae2d550-1`,
proved `20260818.277.1`. Both stock probes accepted the same x86
`DAO.DBEngine.36` provider at `dao360.dll` file version `03.60.9765.0`, SHA-256
`4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`.
