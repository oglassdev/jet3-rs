# Safe core architecture

This note defines the implementation boundaries used before any Jet-specific
format knowledge enters production code. It is architectural guidance, not
format evidence.

## Layers

1. `offset` owns typed byte positions and lengths. Arithmetic and integer
   conversions are checked here.
2. `limits` currently owns caller-selected read ceilings and the non-copy
   `ReadBudget` shared by every source and cursor in one operation. Future
   format parsers must add the broader `ResourceLimits` counters required for
   allocations, traversal, page visits, decoded values, and chain depth.
3. `binary` decodes primitive values from borrowed byte slices. It does not
   perform I/O or know Jet constants.
4. `source` provides bounded random access to a captured-length input. It does
   not read an entire input into memory.
5. Future physical-format modules may depend on these layers. They must keep
   constants beside a `SRC-`, `OBS-`, or `EXP-` provenance ID and must not put
   unchecked binary operations into higher-level database operations.

Jet-specific modules must not duplicate range arithmetic, slice indexing, or
budget charging. The current read budget is not a substitute for the aggregate
allocation and traversal budget required before a parser acts on an
input-derived count or length.

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
walking, or an input-derived allocation without checking both the relevant
specific limit and the future operation-wide resource budget.

## Review rule

The physical reader, writer, and independent structural verifier may share
these safe primitives, but the verifier must not reuse writer encoders or infer
validity from a successful self-read. DAO compatibility remains a separate,
commit-bound evidence claim.
