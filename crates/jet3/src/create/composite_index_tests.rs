use super::initial_index_tests::*;
use crate::WriteError;
use crate::testkit::create;
use crate::testkit::table;
use crate::{
    ColumnSpec, ColumnType, ComposeError, IndexDirection, IndexKind, IndexSpec, PageNumber,
    RowValue, TableRows, TableSpec, create::api_tests::*, definition::column_writer::nz,
};
use std::fs;

#[test]
fn descending_signed_boundaries_encode_and_sort_with_original_locators() -> TestResult {
    let directory = TempDir::new("create")?;
    let indexes = [IndexSpec {
        fields: &[field(0, IndexDirection::Descending)],
        ..one_index(IndexKind::Unique)[0]
    }];
    let table = table(b"Items", &[ID], &indexes);
    let rows: &[&[RowValue<'_>]] = &[
        &[RowValue::Long(i32::MIN)],
        &[RowValue::Long(-1)],
        &[RowValue::Long(0)],
        &[RowValue::Long(i32::MAX)],
    ];
    create(directory.target(), &[TableRows { table, rows }])?;
    let index = tree(&directory.target())?;
    for (entry, (key, slot)) in index.entries().iter().zip([
        ([0x80, 0, 0, 0, 0], 3),
        ([0x80, 0x7f, 0xff, 0xff, 0xff], 2),
        ([0x80, 0x80, 0, 0, 0], 1),
        ([0x80, 0xff, 0xff, 0xff, 0xff], 0),
    ]) {
        assert_eq!(entry.key().raw_bytes(), key);
        assert_eq!(
            entry.row(),
            crate::RowLocator::new(PageNumber::new(24), slot)
        );
    }
    assert!(matches!(
        create(
            directory.target(),
            &[TableRows {
                table,
                rows: &[rows[0], rows[0]]
            }]
        ),
        Err(WriteError::Compose(
            ComposeError::DuplicateInitialIndexKey { value: i32::MIN }
        ))
    ));
    Ok(())
}

#[test]
fn mixed_components_respect_declared_order_and_count_complete_duplicate_keys() -> TestResult {
    for directions in [
        [IndexDirection::Ascending, IndexDirection::Descending],
        [IndexDirection::Descending, IndexDirection::Ascending],
    ] {
        let directory = TempDir::new("create")?;
        let indexes = [IndexSpec {
            fields: &[field(1, directions[0]), field(0, directions[1])],
            ..one_index(IndexKind::Ordinary)[0]
        }];
        let table = table(b"Items", &[ID, SEQUENCE], &indexes);
        let rows: &[&[RowValue<'_>]] = &[
            &[RowValue::Long(i32::MIN), RowValue::Long(i32::MAX)],
            &[RowValue::Long(i32::MAX), RowValue::Long(i32::MIN)],
            &[RowValue::Long(0), RowValue::Long(-1)],
            &[RowValue::Long(1), RowValue::Long(-1)],
            &[RowValue::Long(0), RowValue::Long(-1)],
        ];
        create(directory.target(), &[TableRows { table, rows }])?;
        let index = tree(&directory.target())?;
        let (slots, first_key) = if directions[0] == IndexDirection::Ascending {
            ([1, 3, 2, 4, 0], [0x7f, 0, 0, 0, 0, 0x80, 0, 0, 0, 0])
        } else {
            ([0, 2, 4, 3, 1], [0x80, 0, 0, 0, 0, 0x7f, 0, 0, 0, 0])
        };
        assert_eq!(index.entries()[0].key().raw_bytes(), first_key);
        assert_eq!(
            index
                .entries()
                .iter()
                .map(|entry| entry.row().slot())
                .collect::<Vec<_>>(),
            slots
        );
        let bytes = fs::read(directory.target())?;
        assert_eq!(
            &bytes[20 * crate::PAGE_BYTES + 47..20 * crate::PAGE_BYTES + 51],
            &4_u32.to_le_bytes()
        );
        for kind in [IndexKind::Primary, IndexKind::Unique] {
            let indexes = [IndexSpec { kind, ..indexes[0] }];
            let table = TableSpec {
                indexes: &indexes,
                ..table
            };
            assert!(matches!(
                create(directory.target(), &[TableRows { table, rows }]),
                Err(WriteError::Compose(
                    ComposeError::DuplicateInitialCompositeIndexKey { values: [-1, 0] }
                ))
            ));
            assert_eq!(fs::read(directory.target())?, bytes);
        }
    }
    Ok(())
}

#[test]
fn composite_capacity_and_multiple_row_pages_preserve_locators_and_destination() -> TestResult {
    let directory = TempDir::new("create")?;
    let indexes = [IndexSpec {
        fields: &[
            field(0, IndexDirection::Ascending),
            field(1, IndexDirection::Descending),
        ],
        ..one_index(IndexKind::Unique)[0]
    }];
    let table = TableSpec {
        validation: crate::TableValidation::NONE,
        name: b"Items",
        columns: &[
            ID,
            SEQUENCE,
            ColumnSpec::new(b"Payload", ColumnType::Text { max_len: nz(255) }),
        ],
        indexes: &indexes,
    };
    let payload = [b'x'; 255];
    let values = (0..129)
        .map(|value| {
            [
                RowValue::Long(value),
                RowValue::Long(-value),
                RowValue::Text(&payload),
            ]
        })
        .collect::<Vec<_>>();
    let rows = values.iter().map(|row| row.as_slice()).collect::<Vec<_>>();
    create(
        directory.target(),
        &[TableRows {
            table,
            rows: &rows[..128],
        }],
    )?;
    let index = tree(&directory.target())?;
    assert_eq!(index.entries().len(), 128);
    for (ordinal, entry) in index.entries().iter().enumerate() {
        assert_eq!(
            entry.row(),
            crate::RowLocator::new(
                PageNumber::new(24 + ordinal as u64 / 7),
                (ordinal % 7) as u8
            )
        );
    }
    let original = fs::read(directory.target())?;
    assert_eq!(
        &original[23 * crate::PAGE_BYTES + 2..23 * crate::PAGE_BYTES + 4],
        &8_u16.to_le_bytes()
    );
    let expanded = TempDir::new("create")?;
    create(expanded.target(), &[TableRows { table, rows: &rows }])?;
    assert_eq!(tree(&expanded.target())?.nodes().len(), 3);
    assert_eq!(tree(&expanded.target())?.entries().len(), 129);
    let mut changed = original;
    changed[23 * crate::PAGE_BYTES + 248 + 9] ^= 1;
    fs::write(directory.target(), changed)?;
    assert!(
        crate::create::check::check_initial_rows(
            &directory.target(),
            &table,
            &rows[..128],
            &mut budget()
        )
        .is_err()
    );
    Ok(())
}
