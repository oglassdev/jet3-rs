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
- [DAO_PROVIDER_BLOCKER.md](DAO_PROVIDER_BLOCKER.md) records the currently
  audited external provider boundary. It is not compatibility evidence.

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

Capabilities begin as `not_started` and `unverified`. Statuses must be advanced
only by a change that also adds the referenced evidence; partial foundation
work remains experimental until its declared verification requirement is met.
