# A1 run 12 `ambiguous_record_boundary` diagnosis

## Scope and conclusion

I inspected the retained bundle read-only at:

`/private/tmp/claude-501/-Users-oglass-Development-Misc-access97-rs/77df2993-62f0-4041-97d5-19885072a109/scratchpad/run12/windows-dao-a1-bundle-947038265f6898c55b39da99340220e548836594-20260821T132025Z-a1-gh32486063559-1`

The analyzer source examined is `origin/main` at the bundle's producer commit
`947038265f6898c55b39da99340220e548836594`. I read the 142 replica-1/2 page
indexes and only the 38 distinct page-1 blobs they reference (77,824 bytes).
No other page blob, including page 6, was opened; page-6 results below use only
the retained ordered hashes.

The exact firing predicate is **`surviving_record_pages`, not
`_record_interval`, `_inline_extent`, or `require_unique_boundaries`**.
For replica 1, `surviving_record_pages(view)` returns the empty set and
`derive()` raises `Abort(AMBIGUOUS_RECORD_BOUNDARY)` at `a1_model.py:535-536`
because `len(pages) != 1`. The production list comprehension stops there, so
replica 2 is not derived. A separate read-only evaluation of replica 2 produces
the same empty set from byte-identical page-1 checkpoint states.

The primary classification is **(c), an analyzer/acquisition-contract bug**.
`_page_tracks_allocation` requires the entire candidate page at
`D_GROW_0128` to equal the page at `D_REGROW_0128` (`a1_model.py:229`), while
the checked worker makes both checkpoints relative growth operations from
their current closed-file baselines (`A1.Worker.ps1:531-535`). In the retained
data those baselines force 23→151 pages for D_GROW and 151→279 pages for
D_REGROW. Equality of the global allocation-map page is therefore incompatible
with the acquisition schedule. The synthetic fixture concealed this by giving
D_GROW, D_DROP, and D_REGROW the same 140-page count
(`tests/a1_test_bundle.py:55`).

There is also a secondary **(b) analyzer-layout assumption** exposed by a
counterfactual forced-page-1 evaluation: taking the minimum and maximum of all
page-1 changes yields the whole half-open interval `[2, 2048)`, mixing
page-control/record-directory-like changes with tail record changes. That is
not a usable caller-delimited record and produces only header-like used-pointer
candidates and no free-pointer candidate. This secondary problem is not the
predicate that generated the retained report.

## Exact production control path

The result is identical for replicas 1 and 2 unless noted:

| Stage | Replica 1 | Replica 2 (manual read-only evaluation) |
|---|---:|---:|
| All three idle pairs byte-identical | yes | yes |
| Maximum observed page count | 17,764 | 17,764 |
| Pages present at all eight `_page_tracks_allocation` checkpoints | 0–19 (20 pages) | 0–19 (20 pages) |
| Pages satisfying D whole-page ABA | `{6}` | `{6}` |
| Pages changing across L first/last | `{0, 1}` | `{0, 1}` |
| Pages changing across H first/last | `{0, 1}` | `{0, 1}` |
| Intersection of ABA, L, and H predicates | `{}` | `{}` |
| `surviving_record_pages` | `{}` | `{}` |

Page 6 satisfies only the D whole-page ABA predicate. Pages 0 and 1 satisfy
both the L and H change predicates, but neither satisfies D whole-page ABA.
Thus no physical page satisfies the conjunction required at
`a1_model.py:233-240`.

The retained report's `analysis_work_units = 142115` corroborates this exact
early exit: three idle-pair comparisons plus `17,764 × 8 = 142,112` hash
comparisons in `surviving_record_pages`. It read zero page-store bytes, examined
zero candidate models, did not evaluate the holdout, and never called any of
the following:

- `_record_interval` (`a1_model.py:259`);
- `_inline_extent` (`a1_model.py:312`);
- `require_unique_boundaries` (`a1_model.py:573`).

## Page-1 D predicate

Replicas 1 and 2 have identical page-1 hashes at all 71 checkpoints. The key
hashes are:

| Checkpoint | Page-1 SHA-256 |
|---|---|
| E0 / E0R / D_DROP | `ef15ef01d363dd379ecfc1420838e1ae4056bd0be020e94b422203fb7f3cdb5b` |
| D_GROW_0128 | `dfbcacb6c4c495e82c871e99a7318294cef580bf4f41b4123d527f75c5dec4ab` |
| D_REGROW_0128 | `bbf750422eb9e963d613cf61543bd25345d2d0bb4f4e29cb2774bdc459bc53cc` |
| L_REL_0064 | `d4fb3f031744e53f49ab9d16f1a819fe11a1de931b16a99a6357591bb5b4ddea` |
| L_REL_1280 | `2a5a51c1d9df43b9bd4f9f2acf5f55a85b5bea8aa8a7477197d96c4a300363c2` |
| H_REL_0064 | `76c16649cb11a94ec73064e41cd8d704f31c05f9f35e0ff47d5721208429da4d` |
| H_REL_1280 | `976e2ea97bde826e06baff9623f43d6a9031258e1dddd0607a9b9a28f65d9893` |

The D checkpoint arithmetic and observed page-1 differences are:

| Comparison | File pages / D rows | Changed page-1 bytes | Changed bits | Inclusive byte range |
|---|---|---:|---:|---|
| E0 → D_GROW | 20/0 → 151/1,024 | 17 | 131 | 1922–1938 |
| D_GROW → D_DROP | 151/1,024 → 151/0 | 17 | 131 | 1922–1938 |
| D_DROP → D_REGROW | 151/0 → 279/2,048 | 33 | 259 | 1922–1954 |
| D_GROW → D_REGROW | 151/1,024 → 279/2,048 | 17 | 128 | 1938–1954 |

The observed sequence is therefore A/B/A/C, not the B/A/B equality encoded by
`grown == regrown`. D_DROP returns page 1 exactly to E0, D_REGROW restores the
131 earlier changed bits and adds 128 more changed bits. This is deterministic
and identical in both derivation replicas; it is not replica disagreement or
sampling ambiguity.

## Complete observed page-1 change set

For each replica, 38 distinct page-1 blobs occur over the 71 checkpoints. Of
the 67 preregistered non-idle consecutive transitions, 38 change page 1. The
union contains exactly 152 byte offsets:

`2–3, 10–13, 20, 376, 888, 1400, 1716, 1740, 1744, 1748, 1752, 1756, 1760, 1764, 1780, 1783–1784, 1915–1917, 1920–2047`

The exact nonempty transition changes below apply to both replicas. Ranges are
inclusive; every omitted non-idle transition has zero page-1 byte changes.

| Transition | Bytes | Inclusive offsets |
|---|---:|---|
| E0R → D_GROW_0128 | 17 | 1922–1938 |
| D_GROW_0128 → D_DROP | 17 | 1922–1938 |
| D_DROP → D_REGROW_0128 | 33 | 1922–1954 |
| D_REGROW_0128 → L_REL_0064 | 10 | 1954–1963 |
| L_REL_0064 → L_REL_0512 | 57 | 1963–2019 |
| L_REL_0512 → L_REL_0768 | 33 | 2, 10, 12, 1780, 2019–2047 |
| L_REL_0768 → L_REL_0896 | 5 | 2, 10, 12, 1764, 1780 |
| L_REL_0896 → L_REL_0904 | 9 | 2, 10, 12, 1760, 1764, 2044–2047 |
| L_REL_0904 → L_REL_0912 | 2 | 2044–2045 |
| L_REL_0912 → L_REL_0920 | 2 | 2045–2046 |
| L_REL_0920 → L_REL_0928 | 2 | 2046–2047 |
| L_REL_0928 → L_REL_0936 | 9 | 2, 10, 12, 1756, 1760, 2044–2047 |
| L_REL_0936 → L_REL_0944 | 2 | 2044–2045 |
| L_REL_0944 → L_REL_0952 | 2 | 2045–2046 |
| L_REL_0952 → L_REL_0960 | 2 | 2046–2047 |
| L_REL_0960 → L_REL_0968 | 9 | 2, 10, 12, 1752, 1756, 2044–2047 |
| L_REL_0968 → L_REL_0976 | 2 | 2044–2045 |
| L_REL_0976 → L_REL_0984 | 2 | 2045–2046 |
| L_REL_0984 → L_REL_0992 | 2 | 2046–2047 |
| L_REL_0992 → L_REL_1000 | 9 | 2, 10, 12, 1748, 1752, 2044–2047 |
| L_REL_1000 → L_REL_1008 | 2 | 2044–2045 |
| L_REL_1008 → L_REL_1016 | 2 | 2045–2046 |
| L_REL_1016 → L_REL_1024 | 2 | 2046–2047 |
| L_REL_1024 → L_REL_1032 | 9 | 2, 10, 12, 1744, 1748, 2044–2047 |
| L_REL_1032 → L_REL_1040 | 2 | 2044–2045 |
| L_REL_1040 → L_REL_1048 | 2 | 2045–2046 |
| L_REL_1048 → L_REL_1056 | 2 | 2046–2047 |
| L_REL_1056 → L_REL_1064 | 9 | 2, 10, 12, 1740, 1744, 2044–2047 |
| L_REL_1064 → L_REL_1072 | 2 | 2044–2045 |
| L_REL_1072 → L_REL_1080 | 2 | 2045–2046 |
| L_REL_1080 → L_REL_1088 | 2 | 2046–2047 |
| L_REL_1088 → L_REL_1280 | 5 | 2, 10, 12, 1716, 1740 |
| L_REL_1280 → L_DELETE_ALTERNATING | 1 | 2047 |
| L_IDLE_REOPEN → P_ABS_04096 | 9 | 2–3, 10–13, 1400, 1716, 2047 |
| P_ABS_04096 → P_ABS_08192 | 5 | 3, 11, 13, 888, 1400 |
| P_ABS_08192 → P_ABS_12288 | 5 | 3, 11, 13, 376, 888 |
| P_ABS_12288 → P_ABS_16480 | 15 | 2–3, 10–13, 20, 376, 1783–1784, 1915–1917, 1920–1921 |
| H_REL_0896 → H_REL_0904 | 1 | 1784 |

## Counterfactual record and pointer derivation

These values are diagnostic only: production never reaches these functions.
Forcing `_record_interval(view, 1)` gives the same half-open interval for both
replicas:

- observed record start: **2**;
- observed record end: **2048**;
- interval length: **2,046 bytes**.

That result follows mechanically because offsets 2 and 2047 both change. The
152-byte change union is sparse and spans page-control/record-directory-like
positions as well as the tail. `_record_interval` merely takes the minimum and
maximum; its `changed <= range(start, end)` condition at line 278 is necessarily
true for those extrema. It does not provide an independent delimiter.

Within this forced whole-page envelope, both replicas yield exactly these
pointer observations:

| Layout | Growth-only (“used”) offsets | Churn-only (“free”) offsets |
|---|---|---|
| `u24le_page_then_u8_slot` | `{3, 13}` | `{}` |
| `u8_slot_then_u24le_page` | `{2, 3, 12}` | `{}` |

The “used” candidates are driven by the small offsets that also appear in the
page-level change union. For example, under `u24le_page_then_u8_slot`, offset 3
decodes as page/slot `262/0 → 261/0 → 259/0 → 257/0 → 262/0` across the P
checkpoints, and offset 13 as `6/0 → 5/0 → 3/0 → 1/0 → 6/0`. No four-byte
window meets the churn-only free-pointer rule. The only page-1 change at
L_DELETE_ALTERNATING is byte 2047, and L_DELETE_ALTERNATING →
L_REINSERT_SAME has no page-1 change.

The forced envelope does find a unique apparent type transition at offset
1915, conversion ordinal 40 (`P_ABS_16480`). It then fails `_type1_slots`:
`L_IDLE_REOPEN` appears to have only slot 32 active with the impossible
32-bit value 4,227,858,432, while the final checkpoint has slots 0 and 1 active
with values 15,136 and 16,352. This is another consequence of treating the
whole-page tail as one pointer-slot array. `_inline_extent` is consequently not
reached. Restricting the diagnostic interval to `[1915, 2048)` produces no
used or free pointer candidates under either preregistered layout.

Replicas 1 and 2 therefore do **not** disagree: their page-1 hashes, change
sets, forced interval, forced map-type transition, and pointer candidate sets
are all identical. They fail to delimit because the analyzer first rejects
every page, and its fallback change-envelope idea would span almost the entire
page rather than one record.

## Required follow-up preregistration

EXP-0038 defines acquisition start as the first retained schema-valid replica
observation. Run 12 satisfies that criterion. Its inherited amendment rule is:
“after the first acquisition starts, any change requires a new experiment id,
plan file, and provenance entry.” Therefore none of the points below can be an
analyzer-only correction under `DAO-A1-ALLOCATION-MAPS-001` or its R2
interpretation amendment.

A follow-up must use a new experiment ID and must specify, before another
acquisition, at least the following:

1. **Make the D transition and acquisition arithmetic consistent.** Either:
   - preserve a literal B/A/B ABA test by recreating D to the same declared
     logical target as D_GROW (for example, exactly the same fixed row IDs and
     row count, without another relative `baseline + 128` file-growth target),
     then predeclare whether equality is required for the selected record or
     for the whole page; or
   - preserve the current 151→279 regrowth schedule, rename it A/B/A/C rather
     than ABA, and predeclare exact non-equality/set-transition predicates.
     The revised rule must not be fitted to Run 12's specific bit offsets.

2. **Define a real bounded record-candidate source.** “Caller-delimited” must
   correspond to retained acquisition data or a finite preregistered candidate
   enumeration. The new plan must state how candidate record starts/ends are
   obtained, how multiple changing records on page 1 are handled, and whether
   a stable endpoint need ever be witnessed by a change. It must not use the
   minimum/maximum of all page changes as a surrogate delimiter.

3. **Apply transition predicates at the declared record level.** Page header
   and record-directory changes must not defeat record equality or become
   pointer candidates. The negative-page search must use the same bounded
   record-level candidate procedure, not whole-page hash equality.

4. **Separate the global-map and TDEF-pointer search if they are separate
   records/pages.** Run 12 has no valid used/free pair inside the diagnostic
   `[1915, 2048)` tail interval. A new design must preregister which retained
   record candidates are searched for used/free pointers and checkpoints that
   force both a growth-only and a delete/reinsert-only transition. It cannot
   infer that location after inspecting the new holdout.

5. **Derive synthetic tests from the acquisition schedule.** In particular,
   D_REGROW's synthetic page count may not equal D_GROW's when the worker and
   observation contract define it as another relative `baseline + 128`
   checkpoint. A contract test should prove that every analyzer equality is
   arithmetically possible under the preregistered checkpoint generator.

6. **Disambiguate terminal reporting.** The new plan/schema should distinguish
   “no physical page satisfies the transition predicates” from “more than one
   record boundary survives,” or explicitly preregister why both map to one
   no-outcome identifier. Run 12 is the former, despite the retained reason
   `ambiguous_record_boundary`.

Run 12 remains a valid retained no-outcome result under its frozen analyzer. It
must not be reclassified as a decisive result by applying a post-acquisition
model to the same holdout.
