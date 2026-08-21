# A2 allocation-map preregistration

`DAO-A2-ALLOCATION-MAPS-001` is a project-authored, DAO-only physical
experiment preregistered as `EXP-0040` on 2026-08-21. No A2 acquisition has
begun, and this directory contains no worker, analyzer, generator, validator,
or workflow implementation.

A2 uses a record-level A/B/A/C set relation for D. Both growth legs use a fresh
post-create baseline plus 128 pages in fixed 32-row batches, and regrowth must
be strictly larger than first growth. Pages are qualified by hashes before
bounded interval enumeration. Fixed prefix sums make interval tests O(1), and
a page-terminal suffix that decodes entirely to not-in-use under the D-selected
polarity resolves the global record end.
Every page seen at any checkpoint remains in the candidate page space; no page
number, byte offset, or changed-byte envelope defines a record.

The D-delimited global-map record owns polarity, conversion, slot activation,
inline boundary, and extended-base hypotheses. D alone selects polarity; L/P/H
growth checks it and evaluates the later global-map layers. A separately
delimited TDEF record carries only growth and delete/reinsert pointer
hypotheses. The report schema retains four layered outcomes so an inconclusive
conversion, base, or TDEF layer cannot erase another decisive result.

The fixed schedule has 25 checkpoints per replica. L is fully deleted and
exactly reinserted so the churn precondition can empty a page. Conversion is
derived over the entire L/P/H growth window rather than assumed at an L phase
boundary. One or two slots may be active at conversion, but two must be active
by `H_REL_0904`. Inline boundaries come from a fixed byte-boundary enumeration,
independent of anchor fill. Failure to discriminate an extended base is local
to that layer.

Each replica runs as an independent matrix job with its own environment
document. The fan-in freezes replicas 1/2 before a separate process validates
replica 3 and emits a bounded receipt, after which holdout analysis may begin.
Post-PR-33 run-11/run-12 progress timings give a 725-second slow-runner
projection. The frozen 1,700-second worker bound gives 2.34x headroom; the
1,625-second complete-campaign estimate remains below the 1,800-second hosted
performance target. The 2,700-second campaign timeout covers the worker bound,
900-second fan-in bound, and 100 seconds of setup and dispatch allowance.

Before acquisition, the future analyzer must pass non-evidential dry runs on
the retained A1 run-12 bundle in explicit legacy-projection mode and fixtures
emitted by the A2 synthetic generator directly from this plan. The generator
must vary every conversion ordinal, both polarities, slot counts 0/1/2, anchor
fill, and record-end slack; generate every analyzer equality; prove one global
record survivor and both growth-only and churn-only transitions; reach every
Abort through a single perturbation; and accept a decisive layered report with
status `decisive_pending_independent_validation`. The run-12 dry run must also
assert the page, candidate, work, and blob ceilings. Results must be disclosed
in a later additive provenance entry before any hosted dispatch.

The five committed files in `design-inputs/`, the retained A1 bundle, and the
run-11/run-12 progress traces are exploratory design inputs only. They are not
A2 evidence, cannot satisfy derivation or holdout predicates, and cannot
advance a capability. No external MDB implementation was used.
