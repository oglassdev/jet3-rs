# A4 scope amendment 001 — analysis work ceiling

- Amends: `a4-scope-approved.md` (SHA-256
  `ead09d9cec961d018ed4845f14d825d2ae8da2d3329f12d6ae9ea2233e4eeeb7`), whose
  proposed caps list `600,000,000 work units`. That file is a byte-for-byte
  copy of the approved brief and is not edited; this note supersedes its
  600,000,000 figure only.
- Approved by: the user's delegate during review pass 6 (P6-B1), recorded in
  the base plan at `preregistration.scope_amendments[0]`.
- Change: `max_analysis_work_units` is raised from 600,000,000 to
  800,000,000. Exact-accept 800,000,000 and reject 800,000,001 are the
  comparator unit tests; the bound remains `conservative_upper`.
- Why: review pass 6 showed that the H4 frozen evidence and canonical identity
  omitted the second derivation replica. Freezing both replicas' physical
  evidence doubles every occurrence-dependent H4 term. Under the review-pass-4
  grammar (165,888 inner tuples) that correction alone would have charged
  694,378,226 units, beyond the approved ceiling; with the non-failing
  field/index contrast (27,648 inner tuples over 1,270 required occurrences
  per replica) the honestly recomputed two-replica H4 maximum is 150,819,706
  units, so the raise provides preregistered headroom rather than being
  required by the recomputed table.
- Timing justification from `EXP-0051`: the retained A3 analysis charged
  134,291,460 work units inside an analysis step of roughly 17 seconds
  (~7.9M units/s). At that rate 800,000,000 units take roughly 101 seconds,
  far inside the retained 900-second fan-in bound and the hard 2,700-second
  campaign bound, both of which are unchanged.
- Every other cap in the approved brief is unchanged.
