# Windows DAO oracle

Microsoft DAO is an optional black-box test oracle. It is never a production
dependency, and no MDB bytes or provider binaries are committed.

The retained tooling has three purposes:

- `protocol/v1_2/` defines the shared read scenario and snapshot contract.
- `scripts/dev/` plus `scripts/windows-dao-dev.py` support private exploratory
  runs in the local Windows VM described by `docs/LOCAL_WINDOWS_VM.md`.
- `scripts/Invoke-DaoReadV12.ps1` is the bounded DAO producer for #98;
  `scripts/dao_read_diff.py` canonicalizes, validates, and compares its output.
- `.github/workflows/windows-dao-hosted.yml` probes the x86 DAO environment
  and, only when explicitly approved against the pinned plan, runs the single
  protocol-v1.2 acquisition.
- `scripts/Invoke-DaoAllocationA9.ps1` is the bounded page-image generator for
  the A9 allocation experiment (#99); `scripts/dao_allocation_a9.py` checks
  its plan, evaluates its artifact, and runs the synthetic dry run.
  `.github/workflows/windows-dao-allocation-a9.yml` hosts it under the same
  gating as the read differential.
- `acquisition/bootstrap-layout.plan.json` retains the consumed acquisition
  rejected by `EXP-0067`; `acquisition/bootstrap-layout-floor.plan.json`,
  consumed by `EXP-0069`, also remains immutable.
  `acquisition/bootstrap-layout-sufficiency.plan.json`,
  `scripts/dev/BootstrapLayout.DevJob.ps1`, and `scripts/bootstrap_layout.py`
  define the active development-only local-VM successor for the unresolved
  correlations and bounded all-groups sufficiency question in #100.
- `acquisition/system-catalog.plan.json`, `scripts/dev/SystemCatalog.DevJob.ps1`,
  and `scripts/system_catalog.py` define the development-only local-VM
  system-catalog semantics experiment for #100: five closed checkpoints per
  replica with DAO-visible catalog metadata, analyzed against pinned
  system-table hypotheses.
- `acquisition/long-value-maps.plan.json` reuses that bounded producer and
  analyzer for the three-checkpoint EXP-0074 successor that tests per-column
  long-value map locators and assigns the final empty-image page role.
- `acquisition/long-value-maps-followup.plan.json` pins the corrected H6
  successor after EXP-0075: exact order-insensitive suffix coverage and the
  per-column owned-page transition against global LVAL page roles.
- `acquisition/bootstrap-composer-semantics.plan.json` and
  `scripts/bootstrap_composer_semantics.py` define the two-checkpoint successor
  that records only the fixed empty-to-Alpha raw page-9 keys, opaque Alpha
  `LvProp` external value, and page-0 transition required by the crate-private
  composer.

- `acquisition/schema-generalization.plan.json`,
  `scripts/dev/SchemaGeneralization.DevJob.ps1`, and
  `scripts/schema_generalization.py` define the six-checkpoint successor that
  resolves the catalog name-key encoding, the per-create catalog and
  access-control rows, the per-create page-zero and appended-page assignment,
  and the long-value property chunk framing that a typed schema planner would
  otherwise guess.
- `acquisition/multiple-indexes.plan.json`,
  `scripts/dev/MultipleIndexes.DevJob.ps1`, and `scripts/multiple_indexes.py`
  define the issue #150 experiment that compares closed empty, one-index,
  two-index, three-index, and composite-index checkpoints across three replicas.
  The analyzer uses `scripts/system_catalog.py` for the pinned catalog decoding;
  the experiment is limited to its exact page-assignment matrix and makes no
  arbitrary-index, writer, compatibility, or support claim.
- `acquisition/extended-names.plan.json`,
  `scripts/dev/ExtendedNames.DevJob.ps1`, and `scripts/extended_names.py`
  define the SHA-256-pinned issue #152 experiment over all defined CP1252 bytes
  above `0x7E`; `EXP-0101` records its accepted bounded result.
- `acquisition/bootstrap-composer-validation.plan.json` is the immutable
  consumed plan for the accepted `EXP-0085` run. Its pins bind the harness at
  that revision, so the client refuses to dispatch it again.
- `acquisition/lvprop-null.plan.json`,
  `scripts/dev/LvPropNull.DevJob.ps1`, and `scripts/lvprop_null.py` define the
  issue #149 successor that compares the fixed accepted Alpha image, an Alpha
  image with a null catalog `LvProp` and empty mapped long-value page, and a
  fresh DAO Alpha control across three replicas. It tests only the bounded
  structural endpoints and does not establish that the mapped page can be
  omitted or infer a general property grammar.
- `acquisition/lvprop-null-schemas.plan.json`,
  `scripts/dev/LvPropNullSchemas.DevJob.ps1`, and
  `scripts/lvprop_null_schemas.py` define the SHA-256-pinned issue #178
  experiment. It compares the accepted Alpha null-`LvProp` image and exact
  indexed and compact-continuation candidates with fresh same-schema controls
  across three replicas. It is limited to those identities and read-only
  endpoints and makes no arbitrary-schema, allocation, or compatibility claim.
- `acquisition/multi-table-create.plan.json`,
  `scripts/dev/MultiTableCreate.DevJob.ps1`, and
  `scripts/multi_table_create.py` define the SHA-256-pinned issue #100
  multi-table experiment. It compares one library-composed candidate holding
  the exact `EXP-0087` Alpha, Beta, Gamma, and Delta tables with a fresh
  same-sequence DAO control across three replicas. It is limited to that
  identity and read-only endpoints and makes no arbitrary-schema, allocation,
  or compatibility claim.

Concluded A1-A4 and M3-M5 experiment machinery was removed after its results
were recorded in `docs/PROVENANCE.md`. Git history is the archive.

## Portable protocol checks

```sh
python3 -B oracle/windows-dao/scripts/build_v1_2_inventory.py --check
python3 -B oracle/windows-dao/scripts/validate_protocol_v1_2.py schemas
python3 -B oracle/windows-dao/scripts/validate_protocol_v1_2.py inventory \
  oracle/windows-dao/protocol/v1_2/scenarios.json
python3 -B oracle/windows-dao/scripts/dao_read_diff.py synthetic-dry-run \
  /tmp/jet3-dao-read-dry-run.json
python3 -B oracle/windows-dao/scripts/dao_allocation_a9.py plan \
  oracle/windows-dao/acquisition/a9-allocation.plan.json .
python3 -B oracle/windows-dao/scripts/dao_allocation_a9.py synthetic-dry-run \
  /tmp/jet3-dao-a9-dry-run.json
python3 -B -m unittest oracle/windows-dao/tests/test_bootstrap_layout.py -v
python3 -B -m unittest oracle/windows-dao/tests/test_bootstrap_composer_semantics.py -v
python3 -B -m unittest oracle/windows-dao/tests/test_schema_generalization.py -v
python3 -B -m unittest oracle/windows-dao/tests/test_multiple_indexes.py -v
python3 -B -m unittest oracle/windows-dao/tests/test_lvprop_null.py -v
python3 -B -m unittest oracle/windows-dao/tests/test_lvprop_null_schemas.py -v
python3 -B -m unittest oracle/windows-dao/tests/test_multi_table_create.py -v
python3 -B -m unittest discover -s oracle/windows-dao/tests -v
```

These checks execute no DAO operation and make no compatibility claim.
The committed `acquisition/read-v1_2.synthetic.json` is the reproducible output
of the synthetic command; it proves only that the comparison harness accepts a
match and rejects a controlled mismatch.

## Hosted acquisition

`oracle/windows-dao/acquisition/read-v1_2.plan.json` is the immutable consumed
plan for the accepted run recorded by `EXP-0064`. Its approved SHA-256 is
`b4a05fc381efdaf56011205063c07232a77d23f99837e021242ee199cda48570`.
It pins the inventory, producer, evaluator, validator, workflow, Rust source,
and dependency lock at the acquired revision; it is not re-pinned or validated
against later working trees. The retained evaluator checks every MDB digest,
Rust coverage verdict, snapshot pair, and comparison projection. Artifact MDB
bytes remain access-controlled and are never committed.

### A9 allocation (#99)

`oracle/windows-dao/acquisition/a9-allocation.plan.json` preregisters the
page-allocation questions (Q1 empty template, Q2 page append, Q3 free-page
reuse, Q4 table-map extension, Q5 index/long-value ownership) that the future
writer needs, and pins the generator, evaluator, probe, process helper, and
workflow. `windows-dao-allocation-a9.yml` uses the same explicit acquisition
approval and exact `plan_sha256` gate and refuses before any DAO mutation
unless all match. Workflow reruns are rejected; a new dispatch is a new
explicit human decision.

The workflow terminates the generator process tree at the preregistered
120-minute wall-clock ceiling. The evaluator requires the manifest to bind
both the approved plan digest and the checked-out source revision.

One job runs three independent replicas. Each checkpoint is a closed-file
capture of raw 2 KiB page images (hex plus SHA-256 per page) written under
`artifacts/dao-allocation-a9/r<n>/Q<k>/`. The evaluator verifies every digest,
decodes only the SRC-0020 / EXP-0051 / EXP-0057 primitives, requires the
structural answers to agree across replicas, and writes `report.json` with
`status` of `accepted` or `no_outcome`; an integrity failure exits non-zero
without a report. The committed `acquisition/a9-allocation.synthetic.json`
proves only that the evaluator accepts a consistent fabricated artifact and
rejects a tampered one.

## Experiment discipline

Group related schemas, boundaries, mutations and controls into reproducible
suites. Record inputs, source revision, provider environment and complete
comparisons. Preregistration, separate plan PRs and per-run approval are not
required. Fix harness or implementation failures and rerun as needed, retaining
every failed outcome. Add accepted observations to `docs/PROVENANCE.md` without
rewriting history.

The historical acquisitions below used immutable preregistered contracts. Their
retained inputs and outcomes keep those original identities.

The issue #151 `definition-continuation` SHA-256-pinned local job used one fresh
empty base per replica and exact 69-, 70-, and 140-field first-create arms to
bracket the 2,048-byte root capacity and cross the root-plus-continuation
capacity by 17 bytes. Its publisher retains an exact ordered prefix and at most
the active arm's bounded recovery image; its analyzer requires complete chain,
page-role, schema, identity, and replica correlation. The preregistration does
not assume consecutive continuation placement or a single-page catalog
`LvProp`. `EXP-0095` records a canonical `no_outcome`: the 69-field arm failed
the combined geometry/64-page capture bound in all three replicas, so no
created-arm bytes or continuation placement were retained.

The SHA-256-pinned `EXP-0098` successor keeps the exact arms and questions,
admits completed checkpoints through 256 pages, and records raw byte length,
divisibility, derived page count, and the exact failed predicate before applying
that policy. Its ordered `arm_baselines` prove all three working copies matched
the empty image before any table append; a copy failure records only the checked
prefix. Ephemeral bases and arms stay in one non-published, non-reparse working
subdirectory, so cleanup failure cannot pollute the retained root inventory.
Recovery-only salvage may retain an aligned active checkpoint,
including `empty` after mutation begins, through 512 pages with exact size/hash
and `interpreted=false`; it is never decoded and every post-mutation failure
remains `no_outcome`. `EXP-0100` records the one authorized acquisition as a
valid `no_outcome`: all three replicas completed the exact four-checkpoint
inventory without recovery, but the 2,046-byte `zero` arm decoded with one
continuation page where the preregistered control required zero. Diagnostic
chains for all three arms are non-promotable. `EXP-0102` replaced that false
control with the established 66-byte `Alpha(Id Long)` shape; `EXP-0103` records
its valid `no_outcome` after every replica encountered one appended page whose
decoded role was `unassigned`. `EXP-0104` preregistered the final analyzer rule:
an explicit `unassigned` page is reportable only when the decoded global map
marks it free, definition and catalog-`LvProp`-referenced LVAL pages must remain
in use, and globally free decoded labels receive no current semantic meaning.

`EXP-0105` records the exact successor run as accepted with all five questions
answered identically across three complete replicas. The 66-, 2,075-, and
4,105-byte definitions use chains `[20]`, `[20, 68]`, and `[20, 219, 218]`.
The wide arms contain globally free `unassigned` tag-9 ranges and globally free,
decoder-labeled LVAL ranges unreferenced by catalog `LvProp`; those retained-byte
labels establish no current owner, purpose, reuse history, or semantic role.
Issue #151 is evidence-complete for the three exact shapes and close-ready, not
a claim about arbitrary schemas, general allocation, writer correctness,
compatibility, or support.

The issue #152 `extended-names` job partitions all 123 defined CP1252 bytes in
`0x80`-`0xFF` into 41 independent three-byte arms per replica. Each defined arm
uses exact singleton, repeated, and neighboring-pair names. A separate controls
arm records exact BSTR append and post-close metadata outcomes for U+007F and
the five undefined-slot Unicode values but is never passed to the catalog
decoder. A passing run retains 43 closed MDBs per replica, 129 total. This is a
bounded catalog-key observation, not a general collation or compatibility
claim. `EXP-0097` records the first run as a canonical `no_outcome`: all DAO
attempts and metadata succeeded, but the old analyzer tried to decode the
former `reject` checkpoint as a physical catalog-key observation. `EXP-0099`
renames that checkpoint `controls` and is the SHA-256-pinned successor with the
41 defined arms unchanged, metadata-only controls, question-bearing replica
equality that excludes incidental locators, and an exact non-ASCII transport
sentinel. `EXP-0101` records the one authorized successor run as `accepted`.
All 123 defined bytes in six exact forms decoded, every attempt and metadata
check succeeded, the sentinel survived both UTF-8 transport hops, and all five
questions agreed across three replicas. U+007F and the five undefined-slot
controls establish only exact Unicode BSTR append and post-close metadata
acceptance. This resolves the bounded #152 experiment question without a
general planner widening. Only the three singleton positions, repeat, and each
byte's registered adjacent defined neighbor were tested, and secondary behavior
was sometimes noncompositional. An implementation must retain the blanket
rejection or fail closed to evidenced or positively composable tested contexts;
more than two non-ASCII bytes, arbitrary names, and untested pairs need more
evidence. The result makes no general collation, writer, compatibility,
public-support, or support-matrix claim.

## Hosted field-update leg

`windows-dao-update.yml` uses the separate `update-scenarios.json` inventory.
Ubuntu creates three before/after pairs through the public creation and
`update_field` APIs: a first field, a later row on another data page, and a
later table. Windows independently locates each requested Long field with the
public reader, verifies its new value and every byte outside its four-byte
span, then snapshots both images with Rust and read-only DAO. Full snapshots
must match the declared before/after rows, schema, Text/Binary payloads, and
an unrelated Memo table, including nulls.

`dao_update_diff.py prepare`, `snapshot`, and `evaluate` retain original and
updated MDBs, logs, identity receipts, and a `matched` or `no_outcome` report.
Acquisition requires a separately committed, pinned EXP-0159 plan; the workflow
rejects repeat attempts. This implementation alone supplies no DAO result or
support-state promotion. Insert/delete, indexed or related targets, null and
variable-width transitions, and hosted stored-query preservation remain outside
this first inventory.

## Required column discovery

`required_columns.ps1` and `required_column_matrix.json` reproduce the native
Required/AllowZeroLength observations in EXP-0283. Run the producer with the
matrix staged as `matrix.json`. The retained bundle includes each run's inbox,
outbox, closed images and original evaluator.

Replay all 504 checkpoints with:

```sh
python3 -B oracle/windows-dao/scripts/required_column_discovery.py \
  --evidence-root /path/to/required-column-discovery \
  --output /path/to/replayed-report.json
```

The portable raw-row helper represents Boolean false as `false`; the original
report used null in that internal raw field. Complete DAO values and all other
reported observations are unchanged. The original report remains retained.
`required_column_prepare.py --help` describes creation and independent mutation
input preparation; native discovery alone does not establish writer compatibility.

`required_column_acceptance.ps1` consumes the preparation ZIP staged as
`required-inputs.zip`: 72 creation pairs and 204 independent mutation pairs.
Each receipt retains complete schema/property arrays, rows, primary traversal
and Seek results, the provider, and closed file identities. The evaluator checks
raw values, named properties, keys/locators/counters, allocation ownership,
unchanged system pages and whole-file refusal preservation. Same-source raw
rows must agree except assigned-null fixed padding (EXP-0283) and assigned
long-value descriptor placement; complete payload bytes remain compared.
Independent creation layouts may differ. Default scalar-only properties may
be absent, but text policies must be explicit (EXP-0284).

With the exact source pins and run inbox/outbox retained under the evidence root:

```sh
python3 -B oracle/windows-dao/scripts/required_column_acceptance.py \
  --evidence-root /path/to/required-column-acceptance \
  --run-id RUN_ID --report /path/to/replayed-acceptance.json
```

The first run exposed an incorrect GUID input adapter and missing explicit
disabled empty-value properties; it is retained as failed evidence. GUID inputs
use display-order bytes, and FixedText inputs retain the exact-width contract.


## Larger relationship graphs

`larger_graph_creation_matrix.json` covers ten graph layouts in two replicas.
The creation producer compares complete native and Rust-created databases.
`larger_graph_prepare.py` and `larger_graph_combine.py` prepare reproducible
mutation checkpoints from either origin. The lifecycle producer applies each
native operation independently of the Rust implementation and captures both
results. The negative matrix records per-table index-capacity refusals and
retains the resulting native bookkeeping defects as failed integrity outcomes.

Replay commands take a retained private bundle; MDB files stay outside git:

```sh
python3 -B oracle/windows-dao/scripts/larger_graph_creation.py --help
python3 -B oracle/windows-dao/scripts/larger_graph_lifecycle.py --help
python3 -B oracle/windows-dao/scripts/larger_graph_negative.py --help
```

Creation and lifecycle replay require `--evidence-root` pointing to the retained
review directory containing `source-pins.json` and preparation receipts. Input
ZIPs and outboxes are explicit arguments. The evaluators verify hashes,
complete DAO snapshots, physical keys and locators, reciprocal relationships,
allocation ownership and refusal preservation. See EXP-0281/0282 for the exact
accepted scope and bundle identities.

The larger-graph lifecycle evaluator also requires `--getter-run` pointing to
a closed supplementary run with `inbox/` and `outbox/` directories.
`larger_graph_property_getters.ps1` consumes the retained
`replication-property-inputs-r3.zip` and calls the actual DAO property-collection
getters for ConflictTable and ReplicaFilter on every initial and output image.
The evaluator checks all 170 image links and every observed property against
the optimized snapshots. A direct TableDef getter is a different accessor and
cannot supply this comparison. EXP-0282 records the accepted complete supplement
and lifecycle replay; earlier failed or provisional reports stay retained.

## Absent and partial column properties

`prepare_column_property_presence.py` derives 36 private inputs from the
EXP-0283 empty native Text, FixedText and Memo schemas. It preserves unrelated
catalog rows and index keys while constructing six property-presence cases:
whole LvProp absence, another field only, Required false/true only, and
AllowZeroLength false/true only. It records source identities, changed pages,
exact property models and local validation results. Local validation alone
does not establish native behavior.

Run `column_property_presence.ps1` with that output's `matrix.json` and
`inputs.zip`. The two replicas produce 288 closed checkpoints for 252 native
insert/update operations. `column_property_presence.py` checks the complete
native observations using `--repo`, `--root`, `--run-id` and `--report`.

`prepare_column_property_presence_acceptance.py` consumes the closed native
outbox and its matrix plus the selected Rust CLI binary and source revision.
Each Rust operation starts from the exact native predecessor image; updates
use full-row replacement, which admits variable fields and null transitions.
The resulting `presence-candidates.zip` contains all 252 Rust outputs and
requests. `column_property_presence_capture.ps1` reads them through DAO,
including the actual collection property getters, in two workers.

`column_property_presence_acceptance.py --help` lists the native run, capture
run and producer inputs needed for replay. It requires complete DAO snapshot
comparisons and raw values, schemas, property records, indexes, ownership maps,
unchanged rows and byte-exact refusals. For the first insertion into wholly
absent-property inputs, a bounded check compares append versus reuse of the
released property page, retaining complete ownership and row/index comparisons.
Retain failed preparation and evaluator
outputs alongside corrected runs. EXP-0285 records acceptance of all 252 pairs
(226 successes and 26 refusals), the independently reproduced complete report,
and the verified durable archive. The tested updates use full-row replacement.


## Scalar relationship acceptance

EXP-0288/0289 cover 38 creation pairs and 494 mutation pairs, including compatible
Text/Binary widths, 255-byte keys, explicit signed zeros and fixed-field changes.
The durable private bundle is `shared/checks/20260916-scalar-relationship-acceptance`.
Replay its complete readbacks and raw storage comparisons with:

```sh
bash oracle/windows-dao/scripts/replay_scalar_relationship_acceptance.sh \
  /path/to/20260916-scalar-relationship-acceptance/evidence \
  /path/to/20260916-scalar-relationship-acceptance/prepared \
  /path/to/jet3-rs /tmp/scalar-relationship-replay
```

The archive retains exact producers, recipes, MDB inputs/outputs, failed attempts,
source/binary pins and historical evaluators. The portable scripts reproduce the
same comparisons; their report evaluator hashes differ from the historical
filenames. `prepare_scalar_relationship_creation.py` and
`prepare_scalar_relationship_lifecycle.py` replay the retained recipes through a
new Rust candidate. Preparation alone does not establish DAO acceptance.


## Composite relationship acceptance

EXP-0290/0291 cover ordered two- and ten-component keys, compatible widths,
mixed-direction parents, reused foreign indexes and null boundaries. The main
suite accepts twelve creations and 264 same-input mutations (142 successes and
122 refusals). EXP-0292/0293 add self-references, partial/swapped endpoint mappings,
shared Boolean foreign keys and physical index ordering: twelve creations and
sixty same-input mutations (32 successes and 28 refusals). The final source
reproduces every main-suite output byte-for-byte.

Place the durable `20260916-composite-relationship-acceptance` and
`20260916-composite-relationship-lifecycle-discovery` archives beside each other,
then replay the complete candidate comparisons without accessing Windows:

```sh
bash /path/to/20260916-composite-relationship-acceptance/replay.sh \
  /tmp/composite-relationship-report.json
```

The report reproduces SHA-256
`69a65d4d3487962c95dd6c0665e1c69476d6cd3674d61252da87f3cb2e0963cf`.
Set `ANALYSIS` to the lifecycle-discovery directory if it is elsewhere. Archives
retain exact inputs, outputs, producers, preparation scripts, source/binary pins,
failed attempts and complete observer dependencies. Successful field updates
preserve unassigned fields and payload descriptors; only explicitly assigned-null
fixed padding and full-replacement payload placement have bounded allowances.
Native refusals retain their observed side effects, while Rust refusals must
preserve the complete input.

The graph archive contains its own complete observer dependencies:

```sh
bash /path/to/20260916-composite-graph-acceptance/replay.sh \
  /tmp/composite-graph-report.json
```

Its report reproduces SHA-256
`a05dacdfaeb282e5a1b2858d638fb90f5e7009c05ed0a962044a5b40f8683fcd`.
The failed Boolean Seek capture remains archived alongside the corrected complete
readback. These finite comparisons do not establish cascade compatibility.
