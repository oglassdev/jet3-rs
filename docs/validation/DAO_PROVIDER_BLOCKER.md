# DAO provider blocker

Status: **BLOCKED — M1 complete; Rust differential evidence remains**

Audit date: 2026-07-25
Latest evidence commit: `c2e5df29bcd5a779d6aa82582513e28b53f76598`

This record tracks the remaining Microsoft DAO execution blocker. A Windows
host and provider are available, M0 has produced a validated commit-bound
bundle, the controlled M1 marshalling experiment is complete, and the full M1
inventory has produced an independently validated commit-bound bundle. Those
results do not advance any untested Rust support-matrix capability.

## Exact blocking condition

M1 is no longer blocked: all seven scenarios and both pairs passed at clean
commit `c2e5df29bcd5a779d6aa82582513e28b53f76598`, and the retained bundle
independently validates. The remaining blocker is a Rust implementation and
DAO-versus-Rust differential scenario set. The support-matrix validator also
intentionally rejects `dao_bundle` evidence until semantic bundle integration
is implemented. Neither condition may be bypassed by citing DAO-only M1
generation/readback.

## Local audit

Windows 11 Pro build 22631 has an x86 `DAO.DBEngine.36` provider whose
`dao360.dll` file version is 03.60.9765.0 and whose SHA-256 is
`4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`.
The checked x86 probe passed disposable `dbVersion30` creation for both
protocol 1.0 and 1.1 records. The x64 Windows PowerShell and PowerShell 7
probes remained blocked because this provider is x86-only.

The corrected M0 runner passed at commit
`416b834b0d786fdf68efa066ab0e38409e443edf`; its retained bundle validated
independently. This proves only the empty-database scenario. The M1 preflight
at the same clean commit bound the complete controlled inventory and ready
provider, then exited `BLOCKED` without creating a database or bundle.

The repository already has a cross-platform workflow at
`.github/workflows/ci.yml`, but dispatching it requires a remote repository and
credentials. Even if dispatched, its `windows-latest` test job must not be
treated as a DAO job unless the repository probe independently proves that a
usable provider is present.

## Hosted-runner audit

GitHub states that its hosted runners are Azure virtual machines and links to
weekly updated installed-software manifests. The Windows Server 2025 and 2022
manifests inspected on 2026-07-23 do not list Microsoft Access, a DAO or ACE
provider, `dao360.dll`, or `ACEDAO.DLL`. Entries for Visual Studio Office
development workloads do not establish an installed Access database provider.
GitHub also notes that operating-system components are not necessarily listed,
so absence from a manifest is not proof that every candidate COM class is
absent. The repository probe remains authoritative.

- GitHub-hosted runners:
  <https://docs.github.com/en/actions/concepts/runners/github-hosted-runners>
- Windows Server 2025 image:
  <https://github.com/actions/runner-images/blob/main/images/windows/Windows2025-Readme.md>
- Windows Server 2022 image:
  <https://github.com/actions/runner-images/blob/main/images/windows/Windows2022-Readme.md>

## Provisioning constraints

The smallest credible primary oracle is an interactive, licensed Windows x64
desktop or VM with a 32-bit Access/DAO provider and 32-bit Windows PowerShell.
The probe must test the candidate registrations and accept only one that
successfully creates and closes a disposable unencrypted database with
`CreateDatabase(..., dbVersion30)`. Provider name alone is insufficient.

Microsoft documents the Microsoft 365 Access Runtime as a free x86/x64
download that includes interfaces including DAO. The same page says the
runtime is not intended as a general replacement for Jet, as a general DBMS
used to create files, or for unattended system-service/server-side use without
a logged-on user. Consequently, this project does not assume that installing
the runtime in an unattended hosted CI job is an acceptable oracle route; an
operator must confirm the intended environment and applicable terms.

The Access Database Engine 2016 Redistributable is not a preferred route:
Microsoft lists its support as ending on 2025-10-14 and recommends Microsoft
365 Access Runtime instead.

- Microsoft 365 Access Runtime:
  <https://support.microsoft.com/en-US/Access/download-and-install-microsoft-365-access-runtime>
- Access Database Engine 2016 Redistributable:
  <https://www.microsoft.com/en-us/download/details.aspx?id=54920>
- DAO `dbVersion30` documentation:
  <https://learn.microsoft.com/en-us/office/client-developer/access/desktop-database-reference/databasetypeenum-enumeration-dao>
- GitHub self-hosted runner registration:
  <https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners>

## Unblocking checklist

1. Supply an interactive Windows host or VM whose licensing permits this
   independent test-oracle use.
2. Install or identify an in-process DAO provider at the process bitness used
   by PowerShell.
3. Run `oracle/windows-dao/scripts/probe-provider.ps1`.
4. Require `status: ready`; retain the provider identity, COM server path and
   hash, architecture, OS, locale, code pages, and probe output.
5. Run and review the controlled M1 marshalling experiment, retaining exact
   PowerShell/CLR/provider versions, input runtime types, `Variant` and
   `AppendChunk` behavior, DAO readback, and failures.
6. Check out the exact clean release commit and run the checked declarative DAO
   scenarios.
7. Validate and return the complete commit-bound bundle specified by
   `docs/validation/EVIDENCE.md`.

Until all seven steps are complete, G3 and every release claim that depends on
DAO remain `BLOCKED`.

## Current oracle implementation boundary

The shared portable protocol core, the six v1 schemas, the eight v1.1 schemas,
and deterministic oracle test suite run on this host. They validate evidence
shape and fail closed; they do not execute DAO or establish compatibility.

The protocol-1.0 PowerShell executor implements the M0
`DAO-GEN-PROBE-001` operation: activate a candidate provider, create an
unencrypted `dbVersion30` database, close and reopen it, export the empty DAO
snapshot, and seal a commit-bound bundle. Its validator deliberately rejects
the differential modes `rust_read_dao`, `dao_open_rust`, and
`dao_verify_rust_update` until their canonical semantic comparison and
preservation checks are implemented. The separate M1 executor below expands
only controlled DAO generation/readback; neither executor establishes general
read, create, update, or Access 97 compatibility.

M1 retains the checked, non-executing preflight and adds a modular executor,
structured DAO adapter, complete-inventory validator, and private same-volume
publisher. The executor binds the exact clean commit, complete controlled
inventory, locked ready environment, x86 registry identity, and provider
binary before COM or output mutation; it rechecks those identities before
publication. The checked experiment at commit
`be8e0c9943fdab088a5a08be956435c897a4a1f2` resolved the external marshalling
boundary: direct `System.Byte[]` assignment is required for `dbBinary`, while
`dbLongBinary.AppendChunk(System.Byte[])` round-trips the complete boundary
ladder. The remaining boundary is executing all seven scenarios and both pairs
from the exact clean pushed executor commit and independently validating the
resulting immutable bundle.
