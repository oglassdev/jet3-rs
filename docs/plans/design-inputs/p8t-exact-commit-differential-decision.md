# P8T exact-commit differential decision

Recorded: 2026-08-28

## Source decision

This decision selects the recommendation in
`docs/plans/design-inputs/sol-diff-proposal.md`, SHA-256
`a29528643e6ebe093b05334ca3d1030d97243ca03fdc5fb216c5a3502aaf28e6`.
That proposal remains the complete comparison of the three considered options.

## Selected path

Option 3, end-to-end semantic traversal, is the advancement path for every
`dao_differential` capability. DAO and Rust must independently produce the
same canonical user-schema, row, and value result for the complete manifested
scenario set. Allocation internals stay out of the shared semantic snapshot;
Rust supplies a separate source-MDB-bound coverage receipt so the intrinsic
adapter can require the registered allocation branches and reject a bypass.

Option 1, checkpoint consequence differential, is a supplemental stress and
coverage lane for `format.pages_allocation_usage`; it cannot independently
advance that capability. Option 2 is reserved for a future exact physical-set
claim and is not part of the v1 advancement path.

The read leg may advance an implemented read capability without waiting for a
writer. Writer and update capabilities require the later DAO-open and update
preservation legs. Semantic equality proves only the declared, manifested
operations; it does not prove that DAO exposed an allocation set, that every
free page was classified exactly, or that Rust reproduces Jet's preferred
allocation strategy.

## Exact-commit publication consequence

The selected differential is release evidence only when the intrinsic
`dao_differential_v1` adapter recomputes it from a detached, immutable bundle
whose manifest and every payload are hash-bound to the exact clean release
commit. Earlier A3, M1, and other observational bundles remain design or
provenance inputs and cannot be relabelled. Missing, skipped, stale, altered,
or incomplete scenarios, branches, snapshots, preservation results, provider
identity, artifact hashes, or commit bindings fail closed.

The repository commit does not contain a reference to a bundle that can exist
only after that commit is frozen. Full acceptance explicitly selects one
detached overlay, validates its manifest and payload hashes against the clean
`HEAD`, runs the intrinsic adapter, and derives the effective verification of
each capability for that acceptance run. The committed support matrix retains
its repository-verifiable baseline; detached evidence is never copied into it
and never silently discovered.
