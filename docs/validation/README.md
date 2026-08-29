# Validation

This directory defines what evidence is required before `jet3-rs` may claim a
capability. Words such as *works*, *supported*, and *compatible* are
conclusions of this contract, not synonyms for "our reader accepts our bytes."

- [EVIDENCE.md](EVIDENCE.md) defines status and evidence vocabulary.
- [ACCEPTANCE.md](ACCEPTANCE.md) defines the checks `scripts/acceptance.sh`
  runs. The v1 release gates are the three listed in
  [`../plans/V1_SCOPE.md`](../plans/V1_SCOPE.md).
- [support-matrix.json](support-matrix.json) is the machine-readable
  capability ledger; `schema/support-matrix.schema.json` fixes the capability
  ids.
- [EXTERNAL_CORPUS.md](EXTERNAL_CORPUS.md) describes the external fixture
  corpus.

Historical experiment write-ups (M1–M5, G6, CI evidence records) live in git
history and in `docs/PROVENANCE.md`; they are not evidence for the current
commit.

## Scope

v1 reads unencrypted Access 97 / Jet 3 databases without a runtime dependency
on Microsoft Access, DAO, ODBC, Java, or a native C library. DAO is a test
oracle only. Everything else is listed under "out of v1" in `V1_SCOPE.md`.

## Claim rules

1. Every capability has one implementation state and one verification state
   in `support-matrix.json`.
2. A round trip through code under test is internal evidence only.
3. A capability is **supported** only when a DAO differential run on the
   released commit matches for it.
4. Missing, skipped, or non-reproducible evidence is a failure, never an
   implicit pass.
5. Compatibility reports identify provider version, OS, architecture, locale,
   code page, scenario ids, fixture hashes, and git commit.
