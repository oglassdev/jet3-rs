# Windows DAO oracle

Microsoft DAO is an optional black-box test oracle, never a production
dependency. MDB bytes, provider binaries and VM images stay outside git; see
`docs/LOCAL_WINDOWS_VM.md` for the VM and `docs/PROVENANCE.md` for every
recorded result.

## Running suites

```sh
python3 oracle/windows-dao/dao.py list
python3 oracle/windows-dao/dao.py run [SUITE...] --out /path/outside/repo/new-run
python3 oracle/windows-dao/dao.py compare /path/outside/repo/new-run/SUITE
python3 oracle/windows-dao/dao.py ps probe.ps1 --with input.mdb --out /path/outside/repo/probe
```

Set `JET3_WINDOWS_SHARED_ROOT` (and, if needed, `JET3_WINDOWS_HOST`, `_PORT`,
`_USER`, `_IDENTITY`, `_REMOTE_SHARED_ROOT`). `run` freezes one `jet3-cli`
per run, stages each VM job in the shared inbox, runs it under x86 Windows
PowerShell, checks the provider against the accepted x86 DAO 3.6 DLL and keeps
every stage's inputs, outboxes, logs and `report.json` under `--out`, including
failures. `compare` re-evaluates retained outputs into a new report file.
Archived external inputs resolve under `$JET3_WINDOWS_SHARED_ROOT/checks` (or
`--archive`) and must match their recorded SHA-256.

## Layout

| Path | Role |
| --- | --- |
| `dao.py` | Runner: VM transport, spec expansion, stages, reports |
| `Common.ps1` | PowerShell helpers every VM script dot-sources |
| `Native.ps1` | Builds native inputs and applies native edits from JSON operations |
| `Observe.ps1` | Complete DAO readback: database, tables, fields, indexes and traversals, rows, relations, QueryDefs |
| `structure.py` | Raw decoder: pages, maps, definitions, rows, payloads, index trees and rebuilt keys |
| `keys.py` | General and six-locale Text key models (EXP-0248, EXP-0309) |
| `compare.py` | Candidate/native comparisons: readback, outcome, residue, raw semantics, placement, preservation, LvProp, continuations |
| `generate.py` | Formulaic suite data referenced by a spec's `generate` |
| `suites/*.json` | One spec per suite; `module` and `registry` suites keep their own staging |
| `suites/storage*` | Storage lifecycle staging and evaluation |
| `registry/` | Byte-model suites driven by `crates/jet3/examples/*_candidate.rs` |
| `protocol/v1_2/` | Scenario and snapshot contract behind `jet3-cli snapshot` |

A spec lists native `inputs` (operations, `extends`, `from` another image or a
retained `external` file), Rust `creations`, edit `cases` (Rust requests with
optional `native` operations, `kind`, `dates`, `placement_roles`,
`native_residue`, `getter_residue`), optional native `continuations`, the
`checks` applied to each kind, and `expected_failures` recorded in PROVENANCE.

Portable tests: `python3 -m unittest discover -s oracle/windows-dao/tests`.

## Suites

Kinds: `spec` suites run entirely from their JSON (and `generate.py`); `module` suites
stage their own lifecycles in `suites/<module>.py`; `registry` suites compare the images
of a `crates/jet3/examples/<example>.rs` candidate with a DAO replay of the same recipe.

| Suite | Kind | EXP | Scope |
| --- | --- | --- | --- |
| `allocation-lifecycle` | registry | 0254, 0256 | Allocation lifecycles past the 1024-page inline map and the 16352-page bitmap slot, for wide rows and 1800-byte Memo/OLE payloads, with damaged-map refusals, native writes and Rust edits of native DAO outputs |
| `creation-tables` | registry | 0222, 0241, 0244, 0249, 0251 | Created Long tables: five to 256 tables with zero to three indexes, catalog page growth, the creation counter and its overflow refusal, exact sequential layout and native inserts into both outputs |
| `definition-chains` | registry | 0247 | Items definitions that exactly fill or just overflow their root and one or two continuation pages, with compact continuation placement, Byte/Long/Memo/OLE rows, indexes, Rust reads of DAO outputs and native writes |
| `index-capacity` | registry | 0252, 0253 | Index capacity: 4, 13, 14 and 32 Long indexes of up to ten components and a mixed ten-type key through edits, regrowth, DAO successors and Rust continuation |
| `index-trees` | registry | 0223, 0225 | Single-Long index trees through split, key reorder, row growth, collapse and regrowth, DAO successors, and Rust continuation on prefix-compressed native trees |
| `indexed-boundary` | registry | 0217, 0218, 0221 | Indexed insertion boundaries: in-page insert, EOF page append and duplicate refusal against one Long primary key |
| `indexed-rows` | registry | 0215, 0216, 0219, 0221 | Indexed row insertion and deletion with next-row and duplicate follow-ups, three DAO replicas per arm |
| `locale-updates` | spec | 0309, 0310 | Row and schema edits, native refusals and native continuations in six locales |
| `long-value-lifecycle` | registry | 0234, 0238, 0240 | Memo/OLE row lifecycles: insert, edit, empty and reinsert phases against a DAO control with released-page reuse, native writes on both outputs, then Rust edits of the native DAO output |
| `multiple-long-values` | registry | 0235, 0236 | Multi-column Memo/OLE creation: four to eight long-value columns, zero to three indexes, generated Ids and later table order, with map, page-count and fragment checks, Rust reads of DAO outputs and native writes |
| `numeric-indexes` | registry | 0230, 0232, 0243, 0245, 0246, 0248, 0250 | Numeric and multiple-index mutations: integral, Currency/Double/Single, deep, Date, Binary, Text and GUID keys across three indexes, DAO successors and Rust continuation |
| `one-to-one-creation` | spec | 0307, 0308 | One-to-one relationships in new databases against native SQL-built equivalents |
| `one-to-one` | spec | 0307, 0308 | One-to-one relationship creation, edits, row constraints and refusals |
| `practical-lifecycle` | registry | 0229, 0242 | Practical Items/Notes lifecycle: load, replace, delete and reinsert Text/Currency/Boolean rows around a Memo sentinel, plus four byte-preserving refusals |
| `relationship-forms` | spec | 0301, 0302 | Relationship attributes, joins, cascades and unenforced relationship lifecycles |
| `storage-churn` | module | 0304, 0305, 0306, 0311 | Repeated delete/refill churn, slot saturation, minimum fixed rows and complete page reuse |
| `storage-preservation` | module | 0237, 0303, 0304 | Storage release/reuse, payload growth, opaque-object preservation, native continuations and constraint failure/rollback images |
| `text-properties` | spec | 0299, 0300 | Validation rules, defaults, descriptions and LvProp storage: creations and edits |
| `wide-rows` | registry | 0257, 0258, 0259 | Wide rows: 2 to 254 variable columns, long fixed Text and mixed Memo/OLE rows with canonical jump bytes, overflow, DAO successors, Rust continuation and malformed-row refusals |

## Archived harnesses

Superseded harnesses were removed after their results were recorded. They are
reproducible at tag `oracle-archive-2026-09`
(`git checkout oracle-archive-2026-09 -- <path>`). Paths below are relative to
`oracle/windows-dao/`; a bare name means `scripts/<name>.{py,ps1}` with
`acquisition/<name>.plan.json`. `.github/...`, `crates/...`, `scripts/dao-check.py` and
`scripts/windows-dao-*.py` are repository-root paths.

| EXP | Harness at `oracle-archive-2026-09` |
| --- | --- |
| 0040-0055 | Hosted A2-A4 campaigns (removed earlier; see git history before the tag) |
| 0056-0062 | `scripts/windows-dao-dev.py` discovery jobs with `scripts/dev/*.DevJob.ps1` |
| 0063-0064 | Hosted read: `scripts/Invoke-DaoReadV12.ps1`, `scripts/dao_read_diff.py`, `acquisition/read-v1_2.plan.json`, `.github/workflows/windows-dao-hosted.yml`, `scripts/remote/Remote.Process.ps1` |
| 0065 | Hosted A9 allocation: `scripts/Invoke-DaoAllocationA9.ps1`, `scripts/dao_allocation_a9.py`, `acquisition/a9-allocation.plan.json`, `.github/workflows/windows-dao-allocation-a9.yml` |
| 0066-0071 | `scripts/bootstrap_layout.py`, `acquisition/bootstrap-layout*.plan.json` |
| 0072-0077 | `scripts/dev/SystemCatalog.DevJob.ps1`, `acquisition/system-catalog.plan.json`, `acquisition/long-value-maps*.plan.json`, `scripts/system_catalog.py` |
| 0078-0085 | `scripts/bootstrap_composer_semantics.py`, `scripts/bootstrap_composer_validation.py` |
| 0086-0087 | `scripts/schema_generalization.py` |
| 0088-0091, 0106-0107 | `scripts/lvprop_null.py`, `scripts/lvprop_null_schemas.py` |
| 0092-0093 | `scripts/multiple_indexes.py` |
| 0094-0105 | `scripts/definition_continuation.py`, `scripts/extended_names.py` |
| 0108-0110 | `scripts/multi_table_create.py` |
| 0111-0140 | `initial_rows`, `row_candidate`, `relationship_create`, `relationship_candidate`, `indexed_rows`, `parameterized_relationships`, `long_key_layout`, `composite_index`, `multi_level_index*`, `multi_table_rows`, `relationship_rows`, `long_value_rows`, `autoincrement_*` |
| 0141-0142, 0153-0154 | Hosted write: `scripts/Invoke-DaoWriteV12.ps1`, `scripts/dao_write_diff.py`, `scripts/hosted_write_reanalysis.py`, `protocol/v1_2/WRITE.md`, `.github/workflows/windows-dao-write.yml` |
| 0143-0150 | `scalar_index_layout`, `scalar_index_remaining`, `scalar_index_reanalysis` |
| 0151-0152, 0163-0164, 0171-0176 | `field_update.py`, `fixed_field_update`, `fixed_field_successor`, `fixed_field_reuse` |
| 0155-0156, 0165-0166 | `nullable_index`, `nullable_index_successor`, `nullable_index_structure.py` |
| 0157-0162, 0167-0170, 0181-0182, 0187-0192 | `row_delete_*`, `row_insert_candidate`, `eof_insert_candidate` |
| 0159-0160, 0173-0174 | Hosted updates: `scripts/Invoke-DaoUpdateV12.ps1`, `scripts/dao_update_diff.py`, `scripts/dao_row_update_diff.py`, `.github/workflows/windows-dao-update.yml`, `windows-dao-row-update.yml` |
| 0177-0180, 0183-0186, 0189-0190 | `indexed_payload_update`, `single_leaf_key*`, `numeric_index*` |
| 0193-0194, 0201-0202 | `multiple_index`, `multiple_index_reanalysis` |
| 0195-0196, 0203-0204, 0211-0214, 0220 | Hosted row allocation, indexed update, row replacement and creation index: `scripts/Invoke-Dao{IndexedUpdate,CreationIndex}V12.ps1`, `scripts/dao_{row_allocation,indexed_update,row_replacement,creation_index}_diff.py`, matching `.github/workflows/windows-dao-*.yml`, `crates/jet3-testkit` fixture binaries |
| 0197-0200, 0205-0210 | `row_update_candidate`, `row_update_successor`, `empty_long_values`, `memo_property`, `memo_candidate` |
| 0241-0250 | Catalog pages and Text/Binary/Date keys: `scripts/catalog_pages_native.{py,ps1}`, `scripts/catalog_keys.py`, `scripts/text_index_collation.py`, `scripts/scalar_index_mutation_rows.py` |
| 0262-0267 | Overflow rows, fixed Text indexes, empty values: `scripts/row_overflow_lifecycle*.py`, `row_overflow_lifecycle.ps1`, `fixed_text_index_lifecycle`, `empty_value_lifecycle` |
| 0268-0275 | Relationships: `scripts/relationship_mutation_*`, `scripts/rich_relationship_*`, `scripts/query_preservation*`, `scripts/multiple_relationship_*`, `scripts/relationship_graph_*` |
| 0277-0278 | Schema names: `scripts/schema_names_*`, `scripts/schema_name_*.ps1`, `acquisition/schema-names*.matrix.json` |
| 0279-0282 | Relationship indexes, larger graphs: `scripts/relationship_index_*`, `scripts/relationship_system_indexes.py`, `scripts/larger_graph_*`, `acquisition/relationship-indexes.matrix.json` |
| 0283-0285 | Required columns, property presence: `scripts/required_column*`, `scripts/column_property_*`, `scripts/prepare_column_property_presence*` |
| 0286-0289 | Descending parents, scalar relationships: `scripts/*descending_parent*`, `scripts/*scalar_relationship*`, `scripts/replay_scalar_relationship_acceptance.sh` |
| 0297-0298 | Schema edits: `scripts/prepare_schema_candidates.py`, `scripts/compare_schema_*.py`, `scripts/combine_schema_readbacks.py`, `scripts/schema_edit_structure.py`, `scripts/candidate_continuations.ps1`, `scripts/schema_candidate_observer.ps1` |
| 0299-0311 | Per-suite scripts before this harness (`text_property_*`, `relationship_forms_*`, `storage_*`, `one_to_one*`, `locale_*`); the suites themselves are kept above |
| Registry | `scripts/dao-check.py`, `scripts/windows-dao-ps.py`, `scripts/dao_common.py`, `scripts/field_update.ps1`, `scripts/probe-provider.ps1` and the per-suite `scripts/*.py`/`*.ps1` now under `registry/` |
