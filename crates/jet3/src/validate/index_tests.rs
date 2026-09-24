use super::tests::*;
use crate::{
    ByteCount, ColumnSpec, ColumnType, IndexColumnSpec, IndexKind, IndexSpec, PAGE_BYTES,
    PageNumber, ResourceLimits, RowValue, TableRows, TableSpec,
    create::composer::compose_database_with_table_rows, validate::*,
};

fn first_row(bytes: &[u8], table: &TableDefinition) -> TestResult<(RowLocator, usize)> {
    let mut work = budget();
    let mut database = open(bytes, &mut work)?;
    let mut rows = database.rows(table, &mut work)?;
    let row = rows.next_row()?.ok_or("missing first row")?;
    Ok((row.locator(), row.raw_bytes().len()))
}

#[test]
fn ordered_stale_keys_missing_entries_and_duplicate_locators_are_rejected() -> TestResult {
    let original = fixture()?;
    let table = definition(&original, b"Items")?;
    let root = page_start(table.physical_indexes()[0].root());
    let (first, length) = first_row(&original, &table)?;
    for defect in 0..4 {
        let mut changed = original.clone();
        match defect {
            0 => {
                // EXP-0126 Long components retain their order after a uniform offset.
                for entry in 0..3 {
                    changed[root + 248 + entry * 9 + 4] += 5;
                }
            }
            1 => changed[page_start(first.page()) + PAGE_BYTES - length + 1] = 3,
            2 => {
                // EXP-0062: remove the final nine-byte leaf record; retained count is legal.
                changed[root + 22 + 27 / 8] &= !(1 << (27 % 8));
                changed[root + 2..root + 4].copy_from_slice(&(1800_u16 - 18).to_le_bytes());
            }
            _ => changed[root + 248 + 9 + 8] = changed[root + 248 + 8],
        }
        assert!(
            matches!(
                validate(&changed)?,
                Err(ValidationError::Table {
                    source: TableValidationError::IndexContents { index: 0, .. },
                    ..
                })
            ),
            "defect {defect}"
        );
    }
    Ok(())
}

#[test]
fn retained_directory_slots_do_not_make_deleted_rows_valid_index_targets() -> TestResult {
    let mut bytes = fixture()?;
    let table = definition(&bytes, b"Items")?;
    let (first, _) = first_row(&bytes, &table)?;
    let start = page_start(first.page());
    let source = bytes[start..start + PAGE_BYTES].try_into()?;
    let deletion = crate::row::delete_page::remove(
        first.page(),
        table.root(),
        &source,
        first.slot(),
        &mut budget(),
    )?;
    bytes[start..start + PAGE_BYTES].copy_from_slice(deletion.image().as_bytes());
    let count = page_start(table.root()) + 12;
    bytes[count..count + 4].copy_from_slice(&2_u32.to_le_bytes());
    assert!(matches!(
        validate(&bytes)?,
        Err(ValidationError::Table {
            source: TableValidationError::IndexContents {
                index: 0,
                detail: "index references a row outside the live table"
            },
            ..
        })
    ));
    Ok(())
}

#[test]
fn unsupported_key_schemas_report_coverage_and_still_check_membership() -> TestResult {
    let plan = compose_database_with_table_rows(
        &[TableRows {
            table: TableSpec {
                validation: crate::TableValidation::NONE,
                name: b"Items",
                columns: &[
                    ColumnSpec::new(b"Id", ColumnType::Long),
                    ColumnSpec::new(b"Body", ColumnType::Memo),
                ],
                indexes: &[IndexSpec {
                    name: b"ById",
                    kind: IndexKind::Primary,
                    fields: &[IndexColumnSpec::ascending(0)],
                }],
            },
            rows: &[
                &[RowValue::Long(2), RowValue::Memo(b"two")],
                &[RowValue::Long(0), RowValue::Memo(b"zero")],
                &[RowValue::Long(1), RowValue::Memo(b"one")],
            ],
        }],
        &mut budget(),
    )?;
    let mut bytes: Vec<_> = plan
        .pages()
        .iter()
        .flat_map(|p| p.image().as_bytes().iter().copied())
        .collect();
    let table = definition(&bytes, b"Items")?;
    let raw = table.physical_indexes()[0].raw_record();
    let start = page_start(table.root());
    let positions: Vec<_> = bytes[start..start + PAGE_BYTES]
        .windows(raw.len())
        .enumerate()
        .filter_map(|(i, value)| (value == raw).then_some(start + i))
        .collect();
    assert_eq!(positions.len(), 1);
    // EXP-0062: retarget the index descriptor to the uninterpreted Memo column.
    bytes[positions[0]..positions[0] + 2].copy_from_slice(&1_u16.to_le_bytes());
    let report = validate(&bytes)??;
    assert_eq!(report.indexes, 8);
    assert_eq!(report.indexes_with_verified_keys, 7);
    assert_eq!(report.uninterpreted_indexes, 1);
    assert_eq!(report.uninterpreted_index_entries, 3);
    let root = page_start(table.physical_indexes()[0].root());
    bytes[root + 248 + 9 + 8] = bytes[root + 248 + 8];
    assert!(matches!(
        validate(&bytes)?,
        Err(ValidationError::Table {
            source: TableValidationError::IndexContents {
                index: 0,
                detail: "index references a logical row more than once"
            },
            ..
        })
    ));
    Ok(())
}

#[test]
fn required_nulls_and_duplicate_unique_row_values_fail_semantic_validation() -> TestResult {
    let original = fixture()?;
    let table = definition(&original, b"Items")?;
    let (first, length) = first_row(&original, &table)?;
    let start = page_start(first.page()) + PAGE_BYTES - length;
    for (offset, value, expected) in [
        (start + length - 1, 0, "null field in a required index"),
        (start + 1, 0, "duplicate non-null key in a unique index"),
    ] {
        let mut bytes = original.clone();
        bytes[offset] = value;
        assert!(matches!(validate(&bytes)?, Err(ValidationError::Table {
            source: TableValidationError::IndexContents { index: 0, detail }, ..
        }) if detail == expected));
    }
    Ok(())
}

#[test]
fn composite_text_binary_guid_null_policies_and_branch_bounds_are_checked() -> TestResult {
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(
            b"Text",
            ColumnType::Text {
                max_len: std::num::NonZeroU8::MAX,
            },
        ),
        ColumnSpec::new(
            b"Bytes",
            ColumnType::Binary {
                max_len: std::num::NonZeroU8::MAX,
            },
        ),
        ColumnSpec::new(b"Guid", ColumnType::Guid),
    ];
    let keys = [
        IndexColumnSpec::descending(1),
        IndexColumnSpec::ascending(2),
        IndexColumnSpec::descending(3),
    ];
    let indexes = [
        IndexSpec {
            name: b"Composite",
            kind: IndexKind::Ordinary,
            fields: &keys,
        },
        IndexSpec {
            name: b"Primary",
            kind: IndexKind::Primary,
            fields: &[IndexColumnSpec::ascending(0)],
        },
    ];
    let texts: Vec<_> = (0..220)
        .map(|i| format!("row-{i:04}-{}", "long".repeat(25)).into_bytes())
        .collect();
    let values: Vec<_> = texts
        .iter()
        .enumerate()
        .map(|(i, text)| {
            [
                RowValue::Long(i as i32),
                if i % 5 == 0 {
                    RowValue::Null
                } else {
                    RowValue::Text(text)
                },
                if i % 7 == 0 {
                    RowValue::Null
                } else {
                    RowValue::Binary(&[0xaa; 80])
                },
                RowValue::Guid([i as u8; 16]),
            ]
        })
        .collect();
    let rows: Vec<_> = values.iter().map(|row| row.as_slice()).collect();
    let plan = compose_database_with_table_rows(
        &[TableRows {
            table: TableSpec {
                validation: crate::TableValidation::NONE,
                name: b"Items",
                columns: &columns,
                indexes: &indexes,
            },
            rows: &rows,
        }],
        &mut budget(),
    )?;
    let mut bytes: Vec<_> = plan
        .pages()
        .iter()
        .flat_map(|page| page.image().as_bytes().iter().copied())
        .collect();
    let report = validate(&bytes)??;
    assert_eq!(report.indexes_with_verified_keys, 9);
    assert_eq!(report.uninterpreted_index_entries, 0);
    let table = definition(&bytes, b"Items")?;
    let physical = table
        .physical_indexes()
        .iter()
        .find(|index| index.fields().len() == 3)
        .ok_or("composite index")?;
    let root = page_start(physical.root());
    assert_eq!(bytes[root], 3);
    // Preserve node framing but invalidate the separator's scalar shape.
    bytes[root + 248] = 0x12;
    assert!(matches!(
        validate(&bytes)?,
        Err(ValidationError::Table {
            source: TableValidationError::IndexContents { .. },
            ..
        })
    ));
    Ok(())
}

#[test]
fn all_null_omission_and_repeated_nullable_unique_keys_have_complete_coverage() -> TestResult {
    for policy in [
        crate::IndexNullPolicy::Include,
        crate::IndexNullPolicy::IgnoreAllNull,
    ] {
        let plan = compose_database_with_table_rows(
            &[TableRows {
                table: TableSpec {
                    validation: crate::TableValidation::NONE,
                    name: b"Items",
                    columns: &[
                        ColumnSpec::new(b"Id", ColumnType::Long),
                        ColumnSpec::new(b"Value", ColumnType::Long),
                    ],
                    indexes: &[IndexSpec {
                        name: b"Nullable",
                        kind: IndexKind::Unique.with_null_policy(policy),
                        fields: &[IndexColumnSpec::ascending(1)],
                    }],
                },
                rows: &[
                    &[RowValue::Long(0), RowValue::Null],
                    &[RowValue::Long(1), RowValue::Null],
                    &[RowValue::Long(2), RowValue::Long(7)],
                    &[RowValue::Long(3), RowValue::Long(8)],
                ],
            }],
            &mut budget(),
        )?;
        let mut bytes: Vec<_> = plan
            .pages()
            .iter()
            .flat_map(|page| page.image().as_bytes().iter().copied())
            .collect();
        let report = validate(&bytes)??;
        assert_eq!(report.indexes_with_verified_keys, 8);
        assert_eq!(
            report.index_entries,
            if policy == crate::IndexNullPolicy::Include {
                40
            } else {
                38
            }
        );
        let table = definition(&bytes, b"Items")?;
        let (first, _) = first_row(&bytes, &table)?;
        let base = page_start(first.page());
        let end = usize::from(u16::from_le_bytes([bytes[base + 12], bytes[base + 13]]) & 0x1fff);
        // EXP-0061: clear Value's presence bit in physical slot two, retaining its index entry.
        bytes[base + end - 1] &= !2;
        assert!(matches!(
            validate(&bytes)?,
            Err(ValidationError::Table {
                source: TableValidationError::IndexContents { index: 0, .. },
                ..
            })
        ));
    }
    Ok(())
}

#[test]
fn index_row_page_cache_reads_once_and_charges_cached_lookup_work() -> TestResult {
    let mut bytes = fixture()?;
    let table = definition(&bytes, b"Items")?;
    let (first, _) = first_row(&bytes, &table)?;
    let start = page_start(first.page());
    let page = bytes[start..start + PAGE_BYTES].to_vec();
    let next = bytes.len() / PAGE_BYTES;
    for _ in 0..3 {
        bytes.extend_from_slice(&page);
    }
    let mut work = budget();
    let mut database = open(&bytes, &mut work)?;
    let mut cache = crate::index::tree::rows::RowReferenceValidator::default();
    let mut scratch = [0; PAGE_BYTES];
    let before = work.read_budget().total_read().get();
    for offset in [2, 0, 1, 2, 1, 0] {
        cache.validate(
            &mut database,
            table.root(),
            RowLocator::new(PageNumber::new((next + offset) as u64), 2),
            &mut scratch,
            &mut work,
        )?;
    }
    assert_eq!(
        work.read_budget().total_read().get() - before,
        (3 * PAGE_BYTES) as u64
    );
    let mut limited = ResourceBudget::new(ResourceLimits::default().with_max_total_work_units(0));
    assert!(matches!(
        cache.validate(
            &mut database,
            table.root(),
            RowLocator::new(PageNumber::new(next as u64), 0),
            &mut scratch,
            &mut limited
        ),
        Err(IndexTreeError::Resource(
            Error::ResourceLimitExceeded { .. }
        ))
    ));
    assert_eq!(limited.read_budget().total_read().get(), 0);
    Ok(())
}

#[test]
fn numeric_and_text_index_decoding_keep_resource_errors_structured() -> TestResult {
    for (kind, value) in [
        (ColumnType::Long, RowValue::Long(1)),
        (
            ColumnType::Text {
                max_len: std::num::NonZeroU8::MAX,
            },
            RowValue::Text(b"key"),
        ),
    ] {
        let plan = compose_database_with_table_rows(
            &[TableRows {
                table: TableSpec {
                    validation: crate::TableValidation::NONE,
                    name: b"Items",
                    columns: &[ColumnSpec::new(b"Value", kind)],
                    indexes: &[IndexSpec {
                        name: b"ByValue",
                        kind: IndexKind::Ordinary,
                        fields: &[IndexColumnSpec::ascending(0)],
                    }],
                },
                rows: &[&[value]],
            }],
            &mut budget(),
        )?;
        let bytes: Vec<_> = plan
            .pages()
            .iter()
            .flat_map(|page| page.image().as_bytes().iter().copied())
            .collect();
        let table = definition(&bytes, b"Items")?;
        let (row, _) = first_row(&bytes, &table)?;
        let mut work = budget();
        let mut database = open(&bytes, &mut work)?;
        let tree = database.index_tree(&table, 0, &mut work)?;
        let mut limited = ResourceBudget::new(
            ResourceLimits::default().with_max_total_decoded_bytes(ByteCount::new(0)),
        );
        let error = crate::validate::index::validate(
            &mut database,
            &table,
            0,
            &tree,
            &[row],
            &mut limited,
            &mut ValidationReport::default(),
        )
        .err()
        .ok_or("expected exhausted decode budget")?;
        assert!(matches!(
            error,
            TableValidationError::Resource(Error::ResourceLimitExceeded { .. })
        ));
    }
    Ok(())
}
