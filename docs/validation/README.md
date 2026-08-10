# Validation contract

This directory defines what evidence is required before `jet3-rs` may claim a
capability. It is intentionally separate from design and format notes. Words
such as *works*, *supported*, and *compatible* are conclusions of this
contract, not synonyms for “our writer produced bytes that our reader accepts.”

The documents are normative for v1:

- [EVIDENCE.md](EVIDENCE.md) defines status and evidence vocabulary.
- [CI_EVIDENCE.md](CI_EVIDENCE.md) defines commit-bound Linux, macOS, and
  Windows G1 records and explicit aggregate selection.
- [ACCEPTANCE.md](ACCEPTANCE.md) defines measurable quality gates and the
  command that runs them.
- [TRACEABILITY.md](TRACEABILITY.md) maps product requirements to evidence.
- [support-matrix.json](support-matrix.json) is the machine-readable capability
  ledger.
- [evidence-policy.json](evidence-policy.json) is the commit-bound policy for
  detached release-evidence overlays. It fixes resource ceilings, the exact
  clean-worktree interpretation, and the closed adapter inventory. Every
  adapter is currently disabled or forbidden, so this foundation cannot yet
  advance a capability's verification state.
- [schema/support-matrix.schema.json](schema/support-matrix.schema.json), at
  `properties.capabilities.prefixItems`, is the canonical ordered v1 capability
  catalog. It fixes every required capability ID, its in-scope or out-of-scope
  classification, and its required verification level. The mutable states in
  `support-matrix.json` must cover that catalog exactly; deleting, renaming,
  inserting, reordering, or reclassifying a capability fails validation.
- [DAO_PROVIDER_BLOCKER.md](DAO_PROVIDER_BLOCKER.md) records the currently
  audited external provider boundary. It is not compatibility evidence.
- [schema/release-evidence-overlay.schema.json](schema/release-evidence-overlay.schema.json)
  defines the detached exact-commit overlay shape. Validation and staging of an
  overlay do not make its contents evidence unless a code-owned adapter is
  enabled and validates the declared artifact kind and verification level.

The following are non-normative indexes to immutable historical experiments,
not evidence for current-commit compatibility:

- [M1_DAO_EVIDENCE.md](M1_DAO_EVIDENCE.md) records controlled DAO generation
  and readback.
- [M2_PAGE_OBSERVATION.md](M2_PAGE_OBSERVATION.md) records bounded descriptive
  observations over M1 files.
- [M3_REPLICATED_DELTA_EVIDENCE.md](M3_REPLICATED_DELTA_EVIDENCE.md) records
  replicated descriptive DAO deltas.

## Scope of a passing release

A v1 release is limited to unencrypted Access 97 / Jet 3 databases. It must
read, create, update, and validate files without a runtime dependency on
Microsoft Access, DAO, ODBC, Java, MDB Tools, another MDB implementation, or a
native C library. Microsoft DAO is a test oracle only.

The following remain explicitly outside v1:

- forms, reports, VBA, macros, and Access UI objects;
- execution of saved queries;
- passwords and encryption;
- replication behavior beyond lossless preservation of required fields;
- concurrent multi-user Jet locking;
- Jet 4 and ACCDB; and
- in-place crash recovery equivalent to Microsoft Jet.

Preserving an out-of-scope object without interpreting it does not make that
object supported.

## Claim rules

1. Every public capability has one implementation state and one verification
   state in `support-matrix.json`.
2. A successful round trip through code under test is internal evidence only.
3. A writer capability cannot be `dao_differential` until DAO opens the
   produced file and a canonical DAO snapshot matches the expected semantics.
4. An update capability additionally must prove, through DAO snapshots, that
   unrelated semantic data was preserved.
5. A capability is described to users as **supported** only when its declared
   verification requirement is met on the exact commit being released.
6. Missing, skipped, stale, or non-reproducible evidence is a failure, never an
   implicit pass.
7. All compatibility reports identify the provider version, OS, architecture,
   locale, code page, scenario IDs, fixture hashes, and git commit.

Source and test records may cite immutable historical blobs to establish
implementation lineage. Release-gate reports, `independent_report` records,
and `dao_bundle` records must instead bind the exact clean commit being
released; lineage evidence never substitutes for those current-commit runs.

Capabilities begin as `not_started` and `unverified`. Statuses must be advanced
only by a change that also adds the referenced evidence; partial foundation
work remains experimental until its declared verification requirement is met.
