# DAO provider blocker

Status: **BLOCKED**

Audit date: 2026-07-24
Audited commit: `e8b01b2bdd143f5713988560df12092c332e18a1`

This record explains why commit-bound Microsoft DAO evidence cannot currently
be produced. It is a provider-availability record, not compatibility evidence,
and it does not advance any support-matrix capability.

## Exact blocking condition

No Windows host with an independently licensed and provisioned in-process
Microsoft DAO provider has been supplied to this checkout. The current macOS
host cannot execute Windows COM, this checkout has no Git remote through which
its Windows workflow can be dispatched, and the current GitHub-hosted Windows
software manifests do not document Access, DAO, ACE, `dao360.dll`, or
`ACEDAO.DLL` as installed.

The release remains blocked until an interactive Windows environment runs
`oracle/windows-dao/scripts/probe-provider.ps1` and reports `status: ready`,
then runs the required declarative scenarios against the exact clean release
commit and returns a complete evidence bundle.

## Local audit

The audited host is macOS 26.3.1 on arm64. It has no `pwsh`,
`powershell.exe`, Wine, QEMU, UTM, Parallels, VMware, or VirtualBox executable.
Docker, Colima, and Lima are present only as Linux-container or Linux-VM
routes; none was running during the audit, and none can provide Windows COM.
`git remote -v` is empty.

No ready provider environment JSON or DAO evidence bundle exists in this
checkout. The checked scenario inventory contains only the unexecuted
`DAO-GEN-PROBE-001` input.

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
5. Check out the exact clean release commit and run the checked declarative DAO
   scenarios.
6. Validate and return the complete commit-bound bundle specified by
   `docs/validation/EVIDENCE.md`.

Until all six steps are complete, G3 and every release claim that depends on
DAO remain `BLOCKED`.

## Current oracle implementation boundary

The portable protocol validator, its six JSON schemas, and its 20 deterministic
tests run on this host. They validate evidence shape and fail closed; they do
not execute DAO or establish compatibility.

The PowerShell runner currently implements only the M0
`DAO-GEN-PROBE-001` operation: activate a candidate provider, create an
unencrypted `dbVersion30` database, close and reopen it, export the empty DAO
snapshot, and seal a commit-bound bundle. The validator deliberately rejects
the differential modes `rust_read_dao`, `dao_open_rust`, and
`dao_verify_rust_update` until their canonical semantic comparison and
preservation checks are implemented. Even a successful M0 provider run would
therefore prove only that exact empty-database probe, not general read, create,
update, or Access 97 compatibility.
