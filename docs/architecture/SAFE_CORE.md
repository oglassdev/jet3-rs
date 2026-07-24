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
5. `source` provides bounded random access to a captured-length input. It does
   not read an entire input into memory.
6. `atomic` publishes a caller-mutated file through a same-directory private
   copy, read-only validation callback, file synchronization, rename, and
   directory synchronization where supported. Its validator contract is a
   publication safeguard, not independent writer or compatibility evidence.
7. Future physical-format modules may depend on these layers. They must keep
   constants beside a `SRC-`, `OBS-`, or `EXP-` provenance ID and must not put
   unchecked binary operations into higher-level database operations.

Jet-specific modules must not duplicate range arithmetic, slice indexing, or
budget charging. They receive a mutable borrow of the operation's existing
`ResourceBudget`; they must not construct or reset a nested budget to evade a
ceiling.

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

The format-neutral atomic publisher excludes concurrent writers and requires
an existing regular file. Failures before rename leave the original path in
place and clean up the private copy. A failure synchronizing the directory
after rename is reported with `replacement_published = true`: the fully
validated replacement is visible, but crash durability of the directory entry
is uncertain.

Same-directory `rename` and `sync_all` inherit the host operating system and
filesystem guarantees. Network and unusual filesystems may be weaker, and
portable directory synchronization is not available through Rust's standard
library on every platform. These limits prevent the foundation from being
described as a complete Jet update implementation.

## Review rule

The physical reader, writer, and independent structural verifier may share
these safe primitives, but the verifier must not reuse writer encoders or infer
validity from a successful self-read. DAO compatibility remains a separate,
commit-bound evidence claim.
