# Validation

This directory records capability status and the three v1 release gates. Words
such as *works*, *supported*, and *compatible* require DAO differential
evidence; they are not synonyms for "our reader accepts our bytes."

- [EVIDENCE.md](EVIDENCE.md) defines status and evidence vocabulary.
- [ACCEPTANCE.md](ACCEPTANCE.md) describes the three release gates and the
  `quick` and `full` commands.
- [support-matrix.json](support-matrix.json) is the machine-readable
  capability ledger; `schema/support-matrix.schema.json` fixes the capability
  ids.
- [EXTERNAL_CORPUS.md](EXTERNAL_CORPUS.md) describes the external fixture
  corpus.

Historical validation contracts live in git history. Accepted format facts and
experiment outcomes remain in `docs/PROVENANCE.md`.

## Scope

v1 reads unencrypted Access 97 / Jet 3 databases without a runtime dependency
on Microsoft Access, DAO, ODBC, Java, or a native C library. DAO is a test
oracle only. Everything else is listed under "out of v1" in `V1_SCOPE.md`.

## Claim rules

1. Every capability has one implementation state, one verification state, and
   its evidence references in `support-matrix.json`.
2. A round trip through code under test is internal evidence only.
3. A capability is **supported** only when a DAO differential run on the
   released commit matches for it.
4. Missing or invalid DAO evidence is a failure, never an implicit pass.
