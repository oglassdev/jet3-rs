use super::initial_rows_tests::*;
use crate::WriteError;
use crate::testkit::create;
use crate::testkit::{index, table};
use crate::{
    ColumnSpec, ColumnType, ComposeError, DatabaseReader, DatabaseSpec, IndexColumnSpec,
    IndexDirection, IndexKind, IndexSpec, PageNumber, ResourceBudget, ResourceLimits, RowValue,
    TableRows,
    create::{api_tests::*, check::ImageCheckError},
    create_database,
    definition::column_writer::nz,
};
use std::fs;

pub(super) const ID_FIELD: [IndexColumnSpec<'static>; 1] = [field(0, IndexDirection::Ascending)];

pub(super) fn one_index(kind: IndexKind) -> [IndexSpec<'static>; 1] {
    [index(b"ById", &ID_FIELD, kind)]
}

pub(super) fn tree(path: &std::path::Path) -> Result<crate::IndexTree, Box<dyn std::error::Error>> {
    let mut budget = budget();
    let mut database = DatabaseReader::open(path, &mut budget)?;
    let definition = database.table_definition(PageNumber::new(20), &mut budget)?;
    Ok(database.index_tree(&definition, 0, &mut budget)?)
}

#[test]
fn ascending_long_keys_sort_signed_extremes_and_retain_row_locators() -> TestResult {
    let directory = TempDir::new("create")?;
    let indexes = one_index(IndexKind::Primary);
    let table = table(b"Items", &[ID], &indexes);
    let rows: &[&[RowValue<'_>]] = &[
        &[RowValue::Long(i32::MAX)],
        &[RowValue::Long(i32::MIN)],
        &[RowValue::Long(0)],
        &[RowValue::Long(-1)],
    ];
    create(directory.target(), &[TableRows { table, rows }])?;
    let index = tree(&directory.target())?;
    let expected = [
        ([0x7f, 0, 0, 0, 0], 1),
        ([0x7f, 0x7f, 0xff, 0xff, 0xff], 3),
        ([0x7f, 0x80, 0, 0, 0], 2),
        ([0x7f, 0xff, 0xff, 0xff, 0xff], 0),
    ];
    for (entry, (key, slot)) in index.entries().iter().zip(expected) {
        assert_eq!(entry.key().raw_bytes(), key);
        assert_eq!(
            entry.row(),
            crate::RowLocator::new(PageNumber::new(24), slot)
        );
    }
    let bytes = fs::read(directory.target())?;
    assert_eq!(
        &bytes[20 * crate::PAGE_BYTES + 47..20 * crate::PAGE_BYTES + 51],
        &4_u32.to_le_bytes()
    );
    assert!(map_bit(&bytes, 21, 2, 23)?);
    assert!(!map_bit(&bytes, 21, 0, 23)?);
    assert!(map_bit(&bytes, 21, 0, 24)?);
    Ok(())
}

#[test]
fn duplicate_keys_are_distinct_counted_for_ordinary_and_rejected_for_unique() -> TestResult {
    let rows: &[&[RowValue<'_>]] = &[
        &[RowValue::Long(2)],
        &[RowValue::Long(1)],
        &[RowValue::Long(2)],
    ];
    for kind in [IndexKind::Primary, IndexKind::Unique, IndexKind::Ordinary] {
        let directory = TempDir::new("create")?;
        let indexes = one_index(kind);
        let table = table(b"Items", &[ID], &indexes);
        let result = create(directory.target(), &[TableRows { table, rows }]);
        if kind == IndexKind::Ordinary {
            result?;
            let bytes = fs::read(directory.target())?;
            assert_eq!(
                &bytes[20 * crate::PAGE_BYTES + 47..20 * crate::PAGE_BYTES + 51],
                &2_u32.to_le_bytes()
            );
            let index = tree(&directory.target())?;
            assert_eq!(
                index
                    .entries()
                    .iter()
                    .map(|entry| entry.row().slot())
                    .collect::<Vec<_>>(),
                [1, 0, 2]
            );
        } else {
            assert!(matches!(
                result,
                Err(WriteError::Compose(
                    ComposeError::DuplicateInitialIndexKey { value: 2 }
                ))
            ));
            assert!(directory.entries()?.is_empty());
        }
    }
    Ok(())
}

#[test]
fn indexed_payload_rows_can_reference_multiple_data_pages() -> TestResult {
    let directory = TempDir::new("create")?;
    let columns = [
        ID,
        ColumnSpec::new(b"Payload", ColumnType::Text { max_len: nz(255) }),
    ];
    let indexes = one_index(IndexKind::Unique);
    let table = table(b"Items", &columns, &indexes);
    let text = [b'x'; 255];
    let values = (0..20)
        .map(|value| [RowValue::Long(19 - value), RowValue::Text(&text)])
        .collect::<Vec<_>>();
    let rows = values.iter().map(|row| row.as_slice()).collect::<Vec<_>>();
    create(directory.target(), &[TableRows { table, rows: &rows }])?;
    for (key, entry) in tree(&directory.target())?.entries().iter().enumerate() {
        let ordinal = 19 - key;
        assert_eq!(
            entry.row(),
            crate::RowLocator::new(
                PageNumber::new(24 + ordinal as u64 / 7),
                (ordinal % 7) as u8
            )
        );
    }
    Ok(())
}

#[test]
fn leaf_capacity_spills_into_a_branch_root() -> TestResult {
    let directory = TempDir::new("create")?;
    let indexes = one_index(IndexKind::Primary);
    let table = table(b"Items", &[ID], &indexes);
    let values = (0..201)
        .map(|value| [RowValue::Long(value)])
        .collect::<Vec<_>>();
    let rows = values.iter().map(|row| row.as_slice()).collect::<Vec<_>>();
    create(
        directory.target(),
        &[TableRows {
            table,
            rows: &rows[..200],
        }],
    )?;
    let original = fs::read(directory.target())?;
    assert_eq!(tree(&directory.target())?.entries().len(), 200);
    assert_eq!(
        &original[23 * crate::PAGE_BYTES + 2..23 * crate::PAGE_BYTES + 4],
        &[0, 0]
    );
    let grown = directory.join("grown.mdb");
    create(&grown, &[TableRows { table, rows: &rows }])?;
    let expanded = tree(&grown)?;
    assert_eq!(expanded.entries().len(), 201);
    assert_eq!(expanded.nodes().len(), 3);
    assert_eq!(expanded.nodes()[0].depth(), 1);
    assert_eq!(fs::read(grown)?[23 * crate::PAGE_BYTES], 3);
    Ok(())
}

#[test]
fn required_null_keys_fail_before_publication() -> TestResult {
    let directory = TempDir::new("create")?;
    for kind in [
        IndexKind::Primary,
        IndexKind::Ordinary.with_null_policy(crate::IndexNullPolicy::Required),
    ] {
        let indexes = one_index(kind);
        let table = table(b"Items", &[ID], &indexes);
        assert!(matches!(
            create(
                directory.target(),
                &[TableRows {
                    table,
                    rows: &[&[RowValue::Null]]
                }]
            ),
            Err(WriteError::Compose(ComposeError::NullInitialIndexKey {
                row: 0
            }))
        ));
    }
    assert!(directory.entries()?.is_empty());
    Ok(())
}

#[test]
fn candidate_check_rejects_index_owner_and_key_corruption() -> TestResult {
    let directory = TempDir::new("create")?;
    let indexes = one_index(IndexKind::Primary);
    let table = table(b"Items", &[ID], &indexes);
    let rows: &[&[RowValue<'_>]] = &[&[RowValue::Long(1)], &[RowValue::Long(2)]];
    create(directory.target(), &[TableRows { table, rows }])?;
    let original = fs::read(directory.target())?;
    let mut changed = original.clone();
    changed[23 * crate::PAGE_BYTES + 4] = 19;
    fs::write(directory.target(), &changed)?;
    assert!(matches!(
        super::check::check_initial_rows(&directory.target(), &table, rows, &mut budget()),
        Err(ImageCheckError::Index(_))
    ));
    let mut changed = original;
    changed[23 * crate::PAGE_BYTES + 248 + 4] = 0;
    fs::write(directory.target(), &changed)?;
    assert!(matches!(
        super::check::check_initial_rows(&directory.target(), &table, rows, &mut budget()),
        Err(ImageCheckError::Mismatch {
            detail: "initial index entries"
        })
    ));
    Ok(())
}

#[test]
fn index_storage_is_charged_to_the_budget() -> TestResult {
    let directory = TempDir::new("create")?;
    let indexes = one_index(IndexKind::Primary);
    let table = table(b"Items", &[ID], &indexes);
    let mut limited = ResourceBudget::new(
        ResourceLimits::default().with_max_allocation_bytes(crate::ByteCount::new(8)),
    );
    assert!(matches!(
        create_database(
            directory.target(),
            &DatabaseSpec {
                tables: &[TableRows {
                    table,
                    rows: &[&[RowValue::Long(1)]]
                }],
                ..DatabaseSpec::default()
            },
            &mut limited
        ),
        Err(WriteError::Compose(ComposeError::Encoding(
            crate::Error::ResourceLimitExceeded {
                kind: crate::ResourceLimitKind::AllocationBytes,
                ..
            }
        )))
    ));
    assert!(directory.entries()?.is_empty());
    Ok(())
}
