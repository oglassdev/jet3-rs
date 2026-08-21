# A2 allocation-map preregistration

`DAO-A2-ALLOCATION-MAPS-001` is a project-authored, DAO-only physical
experiment preregistered as `EXP-0040` on 2026-08-21. No A2 acquisition has
begun, and this directory contains no worker, analyzer, generator, validator,
or workflow implementation.

A2 replaces A1's incompatible relative D regrowth with a literal record-level
A/B/A/B replay: the worker must retain the first growth's exact row count and
reinsert the same IDs and payloads after drop/recreate. Candidate records come
from the finite set of every half-open byte interval on every qualifying page;
they never come from the minimum and maximum changed bytes. Global-map and
TDEF records are searched separately, and page headers or record-directory
bytes outside a candidate interval cannot participate in equality or pointer
predicates.

The fixed schedule has 24 checkpoints per replica. It retains the exploratory
A1 run-12 transitions needed for D replay, low-target growth and churn, the
full four-checkpoint absolute conversion window, and the high-target 896-to-904
transition while removing repeated fine-growth snapshots. L is fully deleted
and exactly reinserted so pages can actually become free and a churn-only
transition is arithmetically possible. Conversion is derived as the single
transition within the whole preregistered growth window, not assumed to occur
by the end of L. Inline-boundary candidates come from a fixed enumeration of
every byte boundary inside each record, never from an anchor's fill level.

Each replica must run in an independent matrix job. A fan-in job must freeze
the replicas 1/2 candidate set before opening replica 3. The frozen safety
bounds are 1,800 seconds per replica, 900 seconds for fan-in, and 2,700 seconds
for fail-closed campaign termination; the hosted target remains at most 1,800
seconds and the preregistered estimate is 1,680 seconds.

Before acquisition, the future A2 analyzer must pass two non-evidential dry
runs: the retained A1 run-12 bundle in explicit exploratory legacy mode and
fixtures emitted by the future A2 synthetic generator directly from this
plan's checkpoint schedule. This preregistration-only change does not implement
or run either tool, so acquisition remains `BLOCKED`. Their hashes, commands,
and results must be disclosed in a later additive A2 provenance entry.

The A1 bundle and diagnosis informed only the A2 design and dry-run contract.
They are not A2 evidence, cannot satisfy a derivation or holdout predicate, and
cannot advance a capability. No external MDB implementation was used.

A decisive report is a successful retained campaign artifact, not a controller
failure. Its complete bundle must use status
`decisive_pending_independent_validation` and remain capped at
`not_independently_validated` until separate scientific recomputation succeeds.
