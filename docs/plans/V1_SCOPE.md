# v1 scope

This is the only planning document. Feature work is tracked as GitHub issues;
`docs/PROVENANCE.md` is the record of what has been observed and accepted.

## v1 deliverables

v1 is a full read/write implementation of unencrypted Access 97 / Jet 3,
delivered in this order:

1. **Reader** (`crates/jet3`): open a file, enumerate tables and columns,
   stream rows, decode every Jet 3 value type, traverse indexes. Malformed
   input yields structured errors with bounded work.
2. **Writer**: create a new database, define tables/columns/indexes and
   relationships, and insert rows that DAO opens and reads back identically.
3. **Update**: insert, update, and delete rows and edit the schema of an
   existing database while preserving all unrelated data (including objects
   we do not interpret).
4. **DAO differential runs**: one per leg (read, write, update). Rust
   candidates and native DAO outputs for the same inputs are compared
   through the suites in `oracle/windows-dao/`, and the results are recorded
   in `docs/PROVENANCE.md`.
5. **Support matrix** (`docs/validation/support-matrix.json`): per-capability
   status set from those runs. Nothing is called "supported" without one.

The release gates are listed in
[validation/README.md](../validation/README.md#release-gates).

## Explicitly out of v1

- Exact-commit build attestation, evidence overlays, and release-evidence
  adapters beyond the DAO bundles.
- Repository-contract / traceability policing tools.
- Forms, reports, VBA, macros, query execution, passwords, encryption,
  replication semantics, multi-user locking, Jet 4, ACCDB, crash recovery.
- In-place column type/size changes and Memo/OLE indexes.
- Creating databases with a sort order other than General; East Asian and
  other unobserved encodings or collations (existing General, Nordic,
  traditional Spanish, Dutch, Cyrillic and Greek databases are writable).
- Parsing or evaluating Jet validation expressions.

## Remaining work

The reader, creation and update inventories are implemented and recorded
(#98, #100, #112, #367-#369), and the codebase cleanup is done (#380). Every
unsupported request is refused with the file unchanged. Still open, under
roadmap #75:

- Restore the `definition-chains` and `allocation-lifecycle` DAO suites,
  which fail on main (#395).
- Release verification (#370): finish the remaining integrity checks, produce
  the read, write and update DAO differential bundles, and meet all three
  gates on the release commit.
- Valid multi-hop row growth stays refused until native observations exist;
  the EXP-0276 representation is rejected.

Evidence establishes only its recorded revisions and finite recipes; no
whole-v1 compatibility is claimed.
