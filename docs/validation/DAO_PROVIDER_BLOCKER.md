# DAO provider and differential-evidence boundary

Status: **BLOCKED — provider ready; release differential evidence absent**

Audit date: 2026-07-25

This record separates provider availability, historical controlled evidence,
and the evidence required on an exact release commit. The local Windows host
has a usable x86 Microsoft DAO provider. M0 and M1 completed historically, and
M2 analyzed retained M1 output descriptively. None of those earlier-commit
results satisfies G3 for a later release commit.

## Exact current blocker

The external provider is not the current blocker. G3 remains blocked because
the project lacks both:

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
files and assigns no physical format meaning. M3 may create repeated fresh
controlled samples to distinguish stable physical deltas from run-specific
variance, but it remains DAO-only descriptive evidence and cannot satisfy G3.

The protocol-1.0 differential modes `rust_read_dao`, `dao_open_rust`, and
`dao_verify_rust_update` remain rejected until their Rust canonical semantic
comparison and preservation checks are implemented.

## Hosted runners and provisioning

GitHub-hosted Windows runner manifests inspected on 2026-07-23 did not list
Microsoft Access, DAO/ACE, `dao360.dll`, or `ACEDAO.DLL`. Absence from those
manifests is not definitive; the checked disposable-creation probe remains
authoritative.

The smallest credible oracle environment is an interactive, licensed Windows
x64 desktop or VM with a matching x86 DAO provider and 32-bit Windows
PowerShell. This project does not install or redistribute Access, DAO, or ACE
without an operator licensing decision. Microsoft 365 Access Runtime terms
and unattended-use constraints must be evaluated by the operator; provider
registration alone is never accepted without disposable `dbVersion30`
creation.

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
