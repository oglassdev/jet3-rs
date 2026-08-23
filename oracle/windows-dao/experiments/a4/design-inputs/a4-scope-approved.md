# A4 scoping brief: row-anchored allocation traversal into catalog bootstrap

## Decision and dependency order

A4 should replace A3's fixed-absolute-offset model with a row-directory-anchored model, finish the three
currently unsupported Stage 2 steps (map location, type-1 reference following, extended-page base), and use the
resulting owned-page path to test only the minimum catalog facts required by Stage 3. This is a new experiment ID
and base plan, not an A3 revision or reinterpretation. Option 3 in
`/private/tmp/sol-diff-proposal.md` remains the eventual DAO-differential path;
A4 supplies physical provenance only.
## Ranked candidate physical questions

### 1. Where are a table's allocation records, and how are they located? (Stage 2)

- Highest leverage: it replaces `MapLocation` and is prerequisite to catalog
  ownership. Test whether a newly created table's tag-02 TDEF page contains two
  page/row locators (closed candidates: u24-page/u8-row and u8-row/u24-page) to
  bounded rows on a tag-01 data page, and resolve row bounds from the documented
  Jet 3 row directory rather than a frozen absolute byte interval.
- A1 produced no model (`EXP-0039`); `EXP-0040` later showed A1's terminal was an
  analyzer/acquisition mismatch, so A1 assigned no location. A2's apparent
  page-1 `[1915,2048)` result (`EXP-0042`) was downgraded because its plan left
  1,935 starts (`EXP-0043`). A3 independently predicted that interval only for
  the D checkpoints (`EXP-0051`).
- The retained A3 rows explain its later failure: page 1 row 0 starts at 1915
  through `L_REL_0512`, then at 1911 at `L_REL_0768`, 1895 at `L_REL_0896`, and
  1847 at `L_REL_1280`. Freezing `[1915,2048)` therefore stopped representing
  the complete row. The first A3 polarity violation at pages 1021--1023 is at
  that frozen 1,024-bit capacity edge (`EXP-0044`, `EXP-0046`, `EXP-0051`).
- A3's TDEF model also searched the wrong unit: the L role has tag-02 page 23
  and tag-01 page 24 with two row slots. Page 23's only growth-window candidate
  at offset 10 changes `00000000 -> 00000002 -> 00000010 -> ...`, has no
  churn-window partner, and is not a stable physical-page reference. Page 24
  has growth and churn byte windows, but its row starts move 1915 -> 1911 ->
  1847 -> 1843, so no two fixed windows form A3's required minimal TDEF interval.
  This is exactly `A3-TDEF-RECORD-NONE`, not evidence that usage metadata is
  absent (`EXP-0046`, `EXP-0048`, `EXP-0051`).
- Existing A3 growth/delete captures are excellent calibration and the same
  checkpoint machinery can test a fresh prediction, but table association is
  not identifiable because all four tables already existed at A3 `E0`. A4
  needs new `CreateTableDef`/`CreateField` and table-drop/recreate checkpoints.
### 2. Which row is owned/in-use versus available, and how do type-1 slots map? (Stage 2)

- This supplies ownership, `PointerFollowing`, zero/null behavior, and
  `ExtendedPageBase`. Resolve the two row records independently; do not assume
  they are two fixed TDEF fields. Use isolated grow, delete-all, reinsert,
  drop/recreate, and idle legs to assign their roles.
- In A3 data, the role companion pages have exactly two row slots: D page 21,
  L page 24, P page 26, and H page 28. Their row-relative prefixes exhibit the
  documented type-0/type-1 forms: page 26 at `P_ABS_16480` begins row 0 with
  `01 26 06 00 00 e1 3f 00 00`; page 28 at `H_REL_0064` begins row 0 with
  `01 e5 3f 00 00 e6 3f 00 00`. Page 1 at `P_ABS_16480` carries
  `01 00 3a 00 00 e0 3f 00 00` (`EXP-0044`, `EXP-0046`, `EXP-0051`).
- `EXP-0046` measured type-05 pages 14848 and 16352 with bitmap bytes at
  `[4,2048)` and found only the slot-relative 16,352-bit base formula survived
  the A2 calibration. A3 did not test that prediction on its holdout because
  conversion terminated first, so `EXP-0051` explicitly claims no conversion,
  base, or unobserved-slot result.
- Once question 1 is row-relative, the existing P/H absolute/relative growth
  design can answer this without a new DAO API. New schema lifecycle operations
  are still needed to prove per-table ownership rather than global allocation.
### 3. Which allocated object is the catalog root and which pages does it own? (Stage 3)

- This is the first Stage 3 dependency after Stage 2: one unique catalog TDEF,
  its map rows, and an allocation-admitted stream of tag-01 catalog pages.
- A3 `E0` already contains tag-01 page 18 with `MSysObjects` and all four user
  names, but string presence is not record identity or ownership. The initial
  29-page image also has tag-02 pages `[2,3,4,5,20,23,25,27]`; A3 had no
  checkpoint between individual schema creations, so it cannot bind one to
  the catalog (`EXP-0051`; its claims deny general TDEF/catalog layout).
- Needs new DAO operations: empty-database baseline, one-at-a-time
  `CreateTableDef`, field append, index append, second-table create/drop/recreate,
  and a canonical DAO schema snapshot at every closed checkpoint. The replica,
  content-addressed page capture, freeze/holdout, and independent-validator
  machinery are reusable.
### 4. What minimal catalog row fields identify objects and names? (Stage 3)

- Test bounded row records only on pages admitted by question 3. Controlled
  table/field/index creation supplies distinct operation signatures for object
  kind; drop/recreate distinguishes persistent identifiers from names; chosen
  ASCII plus one code-page-discriminating identifier distinguishes the frozen
  name-encoding candidates under a recorded Windows ANSI code page.
- A1--A3 recorded DAO row counts/hashes and raw pages but never a canonical
  per-checkpoint schema delta, object-kind oracle, identifier lifecycle, or
  name-encoding prediction (`EXP-0039`, `EXP-0042`, `EXP-0051`). Existing row
  directory evidence (`SRC-0020`) bounds candidate rows, but new schema DAO
  operations are mandatory.
### 5. Lower-priority questions (defer)

- Physical field types/flags/sizes and index roots unblock Stage 4. A4 may use one field and one index only as
  catalog perturbations; it must not infer their TDEF layouts. Long values, index trees, row
  fixed/variable/null regions, and scalar encodings unblock Stages 4--6 and require new operations after catalog
  bootstrap. They should not consume A4's hypothesis or timing budget.
## Recommended A4 scope: four layered hypotheses

1. **A4-H1, TDEF-to-map-row location.** In each derivation replica, schema
   lifecycle deltas select one tag-02 TDEF per logical table and exactly two
   locators under one frozen page/row layout. Each locator resolves to an
   extant tag-01 page and valid row-directory slot at every applicable
   checkpoint. Freeze table-relative models, then predict the role-rotated
   holdout. `no_outcome`: no/multiple TDEFs; no/multiple locator layouts or
   locator pairs; invalid/missing/deleted target row; replica disagreement; or
   holdout prediction failure.
2. **A4-H2, row identity and map role.** The two located rows independently
   decode as complete type-0/type-1 records at their checkpoint-specific row
   bounds. Exactly one transition model assigns owned/in-use versus available
   using grow, delete-all, reinsert, drop/recreate, and idle legs; record motion
   itself is allowed. `no_outcome`: invalid row directory/flags; unsupported
   tag; no/multiple role assignments; unexplained transition; replica or
   holdout disagreement. H1 may be decisive even if H2 is not.
3. **A4-H3, indirect traversal.** Conditional on H2 reaching a type-1 record,
   zero slots are inactive, each nonzero little-endian u32 is the exact tag-05
   page reference, the bitmap begins at byte 4, and absolute page =
   `slot_ordinal * 16352 + bit_index`. Freeze all active/inactive slots and
   boundary flips before holdout. `no_outcome`: no conversion; no inactive
   slot observation; invalid reference; insufficient base discrimination;
   zero/multiple base formulas; replica or holdout disagreement.
4. **A4-H4, catalog bootstrap.** Conditional on H1--H3, exactly one owned
   object stream predicts every canonical DAO schema delta. Within bounded
   catalog rows, one frozen field model predicts table/field/index kind,
   identifier lifecycle, and name bytes/encoding in holdout. `no_outcome`: no
   or multiple catalog roots/records/field models; schema delta not wholly on
   admitted pages; encoding candidate not discriminated; replica disagreement;
   or holdout failure. Report root/location separately from row-field results
   so a later layer cannot erase earlier success.

Success through H3 lets Rust replace all three Stage 2 `Unsupported` steps and iterate table-owned pages with
checked row locators, references, bases, cycles, and budgets. Success through H4 lets it implement the minimal
streaming Stage 3 catalog bootstrap (user object name/kind/id and referenced TDEF), not Stage 4.
## Draft 25-checkpoint schedule

All checkpoints are closed, quiescent, nonadaptive, DAO-reread, and physically
captured. Rotate equal-length physical names among logical roles in three fresh
replicas; replicas 1/2 derive and freeze before replica 3 is downloaded/opened.

`EMPTY`, `EMPTY_R`; `T1_CREATE_ID`; `T1_ADD_TEXT`; `T1_ADD_INDEX`; `T2_CREATE`;
`T2_DROP`; `T2_RECREATE`; `T3_CREATE`; `T4_CREATE`; `T1_REL_0064`;
`T1_REL_0512`; `T1_REL_0768`; `T1_REL_1280`; `T1_DELETE_ALL`;
`T1_REINSERT_SAME`; `T1_IDLE_R`; `T3_ABS_04096`; `T3_ABS_08192`;
`T3_ABS_12288`; `T3_ABS_16480`; `T4_REL_0064`; `T4_REL_0896`;
`T4_REL_0904`; `T4_IDLE_R`.

At schema checkpoints retain canonical DAO user TableDefs, fields, indexes,
attributes, row counts, and rolling row hashes. The index is a nonunique index
on the existing long field; it is an object-kind perturbation only. Use fixed
32-row growth batches and disclose baseline, threshold, and overshoot as in A3.
## Bounds and reusable machinery

- Reuse three independent matrix workers, content-addressed page stores,
  derivation freeze, unopened holdout receipt, phase-resume analysis, separate
  plan-derived validator, tamper suite, complete manifest, and exact clean
  pushed commit binding from A3 (`EXP-0044`--`EXP-0051`).
- Proposed caps: 3 replicas; exactly 25 checkpoints; 2,048-byte pages; 20,480
  final pages/replica; 200,000 inserted rows; 65,536 changed hashes and unique
  blobs; 2 GiB logical checkpoint reads/replica; 512 MiB page store; 768 MiB
  bundle; 64 MiB JSON; 1,000,000 models; 600,000,000 work units; 16 qualified
  pages/layer. Charge row-slot, locator, reference, bit, and candidate work.
- Retain A3's 1,700 s worker, 900 s fan-in, and hard 2,700 s campaign bounds
  (accept 2,700; reject 2,701). Reject over-bound evidence before manifest
  creation. Dry runs must include moving-row, deleted-row, wrong locator target,
  zero/nonzero slot, base ambiguity, catalog multiplicity, encoding ambiguity,
  replica disagreement, holdout failure, and one-over-resource cases.
## What A4 explicitly does not claim

- No Rust correctness, product support, `dao_differential`, exact allocation-set
  equality, DAO-exposed physical oracle, or support-matrix movement.
- No general Jet 3/Jet 4, provider, locale, encryption, compaction, corruption,
  or unexercised slot/base behavior; name results are limited to the pinned
  code page and manifested identifiers.
- No physical column/index definition, row value, index-node, relationship,
  Memo/OLE/long-value, update/writer, free-space preference, or preservation
  claim. A schema field/index operation is a catalog contrast, not Stage 4.
- No claim from A3 calibration alone. A4 must preregister candidates and
  terminals before fresh acquisition, preserve raw observations, freeze before
  holdout, and record independent recomputation additively.
## Estimated wall-clock from A3 history

- Plan writing: **2--4 h**. A3 moved from A2 closure at 14:29 to base/freeze
  text at 15:08--15:33, but A4 adds a new schema-observation contract.
- Adversarial review and additive revisions: **6--10 h**. A3's R2--R5 chain ran
  from 17:28 to 23:41 (about 6.2 h after the first revision, about 8.5 h after
  the base plan), and exposed sequence, semantics, bounds, binding, and timeout
  gaps.
- Analyzer/generator/independent-validator adaptation plus dry-run review:
  **6--12 h engineering elapsed**; actual full synthetic/replay run should be
  **under 10 min**. Row-relative search is smaller than A3's all-interval scan,
  but catalog canonicalization and new terminal reachability need new fixtures.
- Hosted acquisition: **20--25 min clean; reserve the full 45 min bound**. A3
  retained its manifest at 1,270 s and completed at about 1,321 s. Allow
  **2--4 h contingency** if hosted transport/fan-in behaves like A3's five
  infrastructure-only failed attempts. Overall: roughly **1.5--3 working days**
  from draft through a reviewed retained result.
## Open decisions for the user

1. Approve combined Stage-2-plus-catalog A4 (recommended), or cap A4 at H1--H3 and make H4 a smaller A5.
2. Approve one pinned non-ASCII/code-page discriminator (recommended), or use ASCII and leave encoding blocked.
3. Approve one nonunique index as a catalog object-kind perturbation (recommended), or defer index-kind to A5.
4. Keep three replicas, freeze/holdout, independent validation, and the 2,700 s hard bound (recommended).
