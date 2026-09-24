# Windows DAO oracle

Microsoft DAO is an optional black-box test oracle, never a production
dependency. MDB bytes, provider binaries and VM images stay outside git; see
`docs/LOCAL_WINDOWS_VM.md` for the VM and `docs/PROVENANCE.md` for every
recorded result.

## Running the kept suites

Both runners stage files into the VM's shared inbox and run x86 Windows
PowerShell over SSH. Set `JET3_WINDOWS_SHARED_ROOT` (and, if needed,
`JET3_WINDOWS_HOST`, `_PORT`, `_USER`, `_IDENTITY`, `_REMOTE_SHARED_ROOT`).

**Registry suites.** `scripts/dao-check.py` builds the Rust candidate example,
generates images, runs the matching producer under DAO and compares the
result. Each run writes inputs, logs and reports to a new `--out` directory.

```sh
python3 scripts/dao-check.py --out /path/to/new-run            # every suite
python3 scripts/dao-check.py index-trees wide-rows --out /path/to/new-run
```

Suites: `indexed-boundary`, `indexed-rows`, `creation-tables`,
`definition-chains`, `index-trees`, `practical-lifecycle`, `numeric-indexes`,
`index-capacity`, `allocation-lifecycle`, `wide-rows`,
`multiple-long-values`, `long-value-lifecycle`.

**Prepare/compare suites.** The remaining suites split into a portable
preparation step, one DAO producer run through `scripts/windows-dao-ps.py`
(`just windows-dev-ps <producer.ps1> --with <input> ...`), and a portable
comparison against the retained outbox. Each Python entry point documents its
arguments with `--help`.

| Area | Entry points (`scripts/`) | EXP |
| --- | --- | --- |
| Catalog pages, Text/Binary/Date keys | `catalog_pages_native`, `text_index_collation`, `scalar_index_mutation_rows` | 0241-0250 |
| Overflow rows, fixed Text indexes, empty values | `row_overflow_lifecycle`, `fixed_text_index_lifecycle`, `empty_value_lifecycle` | 0262-0267 |
| Relationships | `relationship_mutation_*`, `rich_relationship_*`, `query_preservation*`, `multiple_relationship_*`, `relationship_graph_*` | 0268-0275 |
| Schema names | `schema_names_*`, `schema_name_*.ps1` | 0277-0278 |
| Relationship indexes, larger graphs | `relationship_index_*`, `relationship_system_indexes`, `larger_graph_*` | 0279-0282 |
| Required columns, property presence | `required_column*`, `column_property_*`, `prepare_column_property_presence*` | 0283-0285 |
| Descending, scalar relationships | `*descending_parent*`, `*scalar_relationship*` | 0286-0289 |
| Schema edits | `prepare_schema_candidates`, `compare_schema_*`, `combine_schema_readbacks`, `schema_edit_structure`, `candidate_continuations.ps1`, `schema_candidate_observer.ps1` | 0297 |
| Text properties, relationship forms | `text_property_*`, `relationship_forms_*` | 0299-0302 |
| Storage preservation and churn | `storage_preservation*`, `storage_churn` | 0303-0306, 0311 |
| One-to-one relationships | `one_to_one*` | 0307-0308 |
| Locales | `locale_*` | 0309-0310 |

Shared code: `system_catalog.py` (bounded catalog decoder),
`multi_level_index_structure.py` (index trees and maps), `dao_common.py`
(hashing, JSON and small index helpers), `catalog_keys.py` (catalog name keys
and LvProp payloads) and `field_update.ps1` (PowerShell helper functions the
producers load).

`protocol/v1_2/` is the scenario-inventory and snapshot contract behind
`jet3-cli snapshot`; its README lists the portable validator commands.

Portable tests: `python3 -m unittest discover -s oracle/windows-dao/tests`.

## Archived harnesses

Superseded harnesses were removed after their results were recorded. They are
reproducible at tag `oracle-archive-2026-09`
(`git checkout oracle-archive-2026-09 -- <path>`). Paths below are relative to
`oracle/windows-dao/`; a bare name means `scripts/<name>.{py,ps1}` with
`acquisition/<name>.plan.json`. `.github/...`, `crates/...` and
`scripts/windows-dao-dev.py` are repository-root paths.

| EXP | Harness at `oracle-archive-2026-09` |
| --- | --- |
| 0040-0055 | Hosted A2-A4 campaigns (removed earlier; see git history before the tag) |
| 0056-0062 | `scripts/windows-dao-dev.py` discovery jobs with `scripts/dev/*.DevJob.ps1` |
| 0063-0064 | Hosted read: `scripts/Invoke-DaoReadV12.ps1`, `scripts/dao_read_diff.py`, `acquisition/read-v1_2.plan.json`, `.github/workflows/windows-dao-hosted.yml`, `scripts/remote/Remote.Process.ps1` |
| 0065 | Hosted A9 allocation: `scripts/Invoke-DaoAllocationA9.ps1`, `scripts/dao_allocation_a9.py`, `acquisition/a9-allocation.plan.json`, `.github/workflows/windows-dao-allocation-a9.yml` |
| 0066-0071 | `scripts/bootstrap_layout.py`, `acquisition/bootstrap-layout*.plan.json` |
| 0072-0077 | `scripts/dev/SystemCatalog.DevJob.ps1`, `acquisition/system-catalog.plan.json`, `acquisition/long-value-maps*.plan.json` (decoder kept as `system_catalog.py`) |
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
