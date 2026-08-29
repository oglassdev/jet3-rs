# DAO provider and differential-evidence boundary

Status: **BLOCKED — hosted provider capability confirmed; release differential evidence absent**

Audit date: 2026-08-21

This record separates a historically observed provider capability, retained
controlled evidence, and the evidence required on an exact release commit. A
local Windows host demonstrated a usable x86 Microsoft DAO provider during the
historical runs below. Actions run `32327232241` subsequently demonstrated a
matching stock provider on `windows-2022` and a distinct patched provider on
`windows-2025`; see `EXP-0036`. M0 and M1 completed historically, M2 analyzed
retained M1 output descriptively, and M3 completed a replicated one-variable
physical-delta campaign. None of those earlier-commit or provider-only results
satisfies G3 for a later release commit.

Actions run `32486063559` has now also completed the full A1 hosted lane from
exact commit `947038265f6898c55b39da99340220e548836594`, retaining and
independently validating the first complete A1 bundle. Its preregistered
analysis returned `no_scientific_outcome` for `ambiguous_record_boundary`; see
`EXP-0039`. This proves the hosted acquisition and bundle-validation lane, not
a Rust capability or release differential result.

## Exact current blocker

Provider discovery and the controlled A1 hosted lane are no longer blocking:
`windows-2022` completed the exact-commit acquisition and independent bundle
validation recorded by `EXP-0039`. G3 remains blocked because the project lacks
both:

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

## A1 acquisition result and boundary

[`32486063559`](https://github.com/oglassdev/jet3-rs/actions/runs/32486063559)
completed `DAO-A1-ALLOCATION-MAPS-001` end to end on `windows-2022`, using the
provider identity proved by run `32439805418`. The campaign retained all 213
checkpoint indexes and three schema-valid replica observations, published the
complete bundle from exact producer commit
`947038265f6898c55b39da99340220e548836594`, and reported status
`independently_validated`. The retained manifest SHA-256 is
`97c1286624a5e02fc7bcfc7b1047986e8a15e3ac8aec22488a1a5b4bfa444381`.

The preregistered analysis did not identify a model. It reported
`no_scientific_outcome` with reason `ambiguous_record_boundary`, examined zero
candidate models, and did not evaluate the holdout. Independent bundle
validation established the retained tree, hashes, schemas, and cross-artifact
bindings; it did not independently recompute or validate a scientific outcome.
Per `EXP-0038`, acquisition has now started because a schema-valid replica
observation is retained. Any analyzer change requires a new experiment ID,
plan, and provenance entry. This no-outcome result assigns no physical-format
meaning, advances no Rust capability, establishes no DAO compatibility, and
does not satisfy the release differential gate.

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

### P8T detached-overlay amendment to item 6

This additive amendment (`EXP-0064`) supersedes only item 6's requirement to
reference the bundle from the committed support matrix. The exact-commit
bundle instead appears in one explicitly selected detached overlay, and its
manifest SHA-256 is supplied to checked acceptance out of band. The hashed
acceptance record retains the overlay hash, manifest hash, exact commit,
adapter outputs, and effective capability results. The committed matrix
retains only `source` and `test` lineage and its repository-verifiable
baseline; it contains no `dao_bundle` reference or detached verification
result.

Items 1–5 remain unchanged. In particular, this amendment does not weaken the
clean exact-commit requirement, pre-acquisition human authorization, provider
and environment re-proof, complete declarative inventory, Rust/DAO snapshot
and preservation binding, atomic publication, independent manifest and
payload validation, or acceptance requirements. The policy, allowlist,
contracts, implementation, and acceptance wiring must exist in the clean
pushed commit before acquisition. Any later provenance entry is only a
historical record of that earlier commit and is not evidence for it.

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
