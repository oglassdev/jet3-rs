# Safe core architecture

This note defines the implementation boundaries used before any Jet-specific
format knowledge enters production code. It is architectural guidance, not
format evidence.

## Layers

1. `offset` owns typed byte positions and lengths. Arithmetic and integer
   conversions are checked here.
2. `limits` owns caller-selected byte-I/O ceilings and the non-copy
   `ReadBudget` shared by every source and cursor in one operation.
3. `resource` owns the immutable operation-wide `ResourceLimits` policy and
   non-copy `ResourceBudget`. One budget persists across the complete public
   operation and accounts for reads, cumulative allocation, individual and
   cumulative decoded values, item work, page visits, chain depth, and total
   non-I/O work.
4. `binary` decodes primitive values from borrowed byte slices. It does not
   perform I/O or know Jet constants.
5. `binary_writer` encodes primitive values into a caller-owned fixed slice.
   It allocates nothing, checks the complete destination before mutation, and
   charges cumulative encoded bytes including rewrites after seeking.
6. `source` provides bounded random access to a captured-length input. It does
   not read an entire input into memory.
7. On Unix, `atomic` publishes a caller-mutated file through a same-directory
   private copy only after read-only validation, file synchronization, and a
   retained-handle/path identity check. Cleanup applies the same identity
   boundary and never unlinks a substituted entry. Its validator contract is a
   publication safeguard, not independent writer or compatibility evidence.
8. Future physical-format modules may depend on these layers. They must keep
   constants beside a `SRC-`, `OBS-`, or `EXP-` provenance ID and must not put
   unchecked binary operations into higher-level database operations.

Jet-specific modules must not duplicate range arithmetic, slice indexing, or
budget charging. They receive a mutable borrow of the operation's existing
`ResourceBudget`; they must not construct or reset a nested budget to evade a
ceiling.

The format-neutral writer accepts only borrowed fixed-capacity output. A write
preflights checked position arithmetic, complete capacity, and both cumulative
encoded-byte and aggregate-work limits before copying. Capacity, position, and
limit failures preserve output bytes, writer position, and every budget
counter. Its integer and floating encoders expose only explicit little-endian
operations. This foundation is not a database writer, creator, or updater and
is not evidence that any emitted bytes are valid Jet.

## Error boundary

Malformed data is an ordinary structured error. Errors distinguish at least:

- an invalid requested range;
- truncated or concurrently shortened input;
- arithmetic or integer-conversion overflow;
- a configured resource limit being exceeded; and
- an underlying I/O failure.

Diagnostic context is static or bounded. Error construction must not copy
attacker-controlled strings or large byte ranges.

## Source boundary

A random-access source captures its length when opened. Every request is
checked against that length before I/O and charged to the shared budget before
the read begins. If a file becomes shorter after opening, the source reports a
short read rather than treating missing bytes as zeroes.

No production parser may use unbounded `read_to_end`, recursive untrusted page
walking, or an input-derived allocation without charging the relevant
operation-wide resource dimension before work begins.

## Publication boundary

The Unix-only format-neutral atomic publisher excludes concurrent writers and
requires an existing regular file. It verifies that the private pathname still
names its retained open file immediately before rename. Failures before rename
leave the original path in place and explicitly attempt to remove the guarded
private copy. Cleanup refuses to unlink a substituted entry. Other cleanup
failures retain both the primary update failure and the secondary cleanup
failure; `Drop` retries removal only while identity still matches but cannot
guarantee or report success. The `DirectorySync` stage alone identifies the
post-publication failure state: the fully validated replacement is visible,
but crash durability of the directory entry is uncertain.

Same-directory `rename` and `sync_all` inherit the host operating system and
filesystem guarantees. Network and unusual filesystems may be weaker.
Non-Unix hosts fail closed because an audited overwrite-replace and
post-replacement durability provider is not implemented. Windows file identity
is obtainable and is not described as the blocker. These limits prevent the
foundation from being described as a complete Jet update implementation.

## Review rule

The physical reader, writer, and independent structural verifier may share
these safe primitives, but the verifier must not reuse writer encoders or infer
validity from a successful self-read. DAO compatibility remains a separate,
commit-bound evidence claim.
