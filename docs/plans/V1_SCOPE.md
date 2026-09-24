# v1 scope

This is the only planning document. Feature work is tracked as GitHub issues;
`docs/PROVENANCE.md` is the record of what has been observed and accepted.

## v1 deliverables

v1 is a full read/write implementation of unencrypted Access 97 / Jet 3,
delivered in this order:

1. **Reader** (`crates/jet3`): open a file, enumerate tables and columns,
   stream rows, decode every Jet 3 value type, traverse indexes. Malformed
   input yields structured errors with bounded work.
2. **Writer**: create a new database, define tables/columns/indexes, and
   insert rows that DAO opens and reads back identically.
3. **Update**: insert, update, and delete rows in an existing database while
   preserving all unrelated data (including objects we do not interpret).
4. **DAO differential runs**: one per leg (read, write, update). Rust and
   DAO each produce a canonical semantic snapshot for the shared scenario
   inventory (`oracle/windows-dao/protocol/`); the snapshots are compared
   and the result recorded in `docs/PROVENANCE.md`.
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

The reader and its hosted differential are complete for the recorded inventory.
The CLI (#104), creation module cleanup (#182) and the recorded relationship,
six-locale schema and storage/preservation inventories (#367-#369) are done.
Still open:

- Creation (#100), updates (#112) and their broader DAO inventories
  (#102/#113), under roadmap #75. Creation and updates remain partial: schema
  combinations, index key types, allocation and relationship mutation are
  restricted, and every unsupported request is refused with the file
  unchanged.
- Implementation simplification (#380), then release verification (#370):
  cover the remaining integrity and DAO inventories and meet all three gates
  on the resulting release commit.
- Valid multi-hop row growth stays refused until native observations exist;
  the EXP-0276 representation is rejected.

Evidence establishes only its recorded revisions and finite recipes; no
whole-v1 compatibility is claimed. EXP-0229 completed the practical Items/Notes
lifecycle milestone, which is not a substitute for the full scope.
