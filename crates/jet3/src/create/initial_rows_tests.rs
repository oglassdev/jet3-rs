use super::api_tests::*;
use crate::WriteError;
use crate::testkit::create;
use crate::testkit::table;
use crate::{
    ColumnSpec, ColumnType, ComposeError, DatabaseSpec, PageNumber, ResourceBudget, ResourceLimits,
    RowValue, RowWriteError, TableRows, TableSpec, create::check::ImageCheckError, create_database,
    definition::column_writer::nz,
};
use std::fs;

pub(super) fn scalar_table() -> TableSpec<'static> {
    table(b"Items", &[ID, CODE], &[])
}

#[test]
fn scalar_rows_publish_and_match_requested_values() -> TestResult {
    let directory = TempDir::new("create")?;
    let target = directory.target();
    let rows: &[&[RowValue<'_>]] = &[
        &[RowValue::Long(1), RowValue::Text(b"one")],
        &[RowValue::Long(-2), RowValue::Text(b"two")],
        &[RowValue::Null, RowValue::Null],
    ];
    create(
        &target,
        &[TableRows {
            table: scalar_table(),
            rows,
        }],
    )?;
    super::check::check_initial_rows(&target, &scalar_table(), rows, &mut budget())?;
    let bytes = fs::read(&target)?;
    assert_eq!(bytes.len(), 24 * crate::PAGE_BYTES);
    assert_eq!(
        &bytes[20 * crate::PAGE_BYTES + 12..20 * crate::PAGE_BYTES + 16],
        &3_u32.to_le_bytes()
    );
    assert_eq!(directory.entries()?, ["created.mdb"]);
    // Empty initial rows do not allocate a data page.
    let empty = directory.join("empty.mdb");
    create(&empty, &[TableRows::empty(scalar_table())])?;
    assert_eq!(fs::metadata(empty)?.len(), 23 * crate::PAGE_BYTES as u64);
    Ok(())
}

#[test]
fn rows_with_wrong_types_leave_no_file() -> TestResult {
    let directory = TempDir::new("create")?;
    assert!(matches!(
        create(
            directory.target(),
            &[TableRows {
                table: scalar_table(),
                rows: &[&[RowValue::Byte(1), RowValue::Null]]
            }]
        ),
        Err(WriteError::Compose(ComposeError::Row(
            RowWriteError::TypeMismatch { ordinal: 0, .. }
        )))
    ));
    assert!(matches!(
        create(
            directory.target(),
            &[TableRows {
                table: scalar_table(),
                rows: &[&[]]
            }]
        ),
        Err(WriteError::Compose(ComposeError::Row(
            RowWriteError::ValueCountMismatch { .. }
        )))
    ));
    assert!(directory.entries()?.is_empty());
    Ok(())
}

#[test]
fn initial_row_check_detects_wrong_values_and_counts() -> TestResult {
    let directory = TempDir::new("create")?;
    let rows: &[&[RowValue<'_>]] = &[&[RowValue::Long(1), RowValue::Text(b"one")]];
    create(
        directory.target(),
        &[TableRows {
            table: scalar_table(),
            rows,
        }],
    )?;
    for (rows, detail) in [
        (
            &[&[RowValue::Long(2), RowValue::Text(b"one")][..]][..],
            "initial row value",
        ),
        (&[][..], "initial row count"),
        (&[rows[0], rows[0]][..], "initial row count"),
    ] {
        assert!(
            matches!(super::check::check_initial_rows(&directory.target(), &scalar_table(), rows, &mut budget()),
            Err(ImageCheckError::Mismatch { detail: actual }) if actual == detail)
        );
    }
    Ok(())
}

pub(super) fn map_bit(
    bytes: &[u8],
    map_page: u64,
    row: u8,
    target: u64,
) -> Result<bool, Box<dyn std::error::Error>> {
    let start = map_page as usize * crate::PAGE_BYTES;
    let raw = bytes[start..start + crate::PAGE_BYTES].try_into()?;
    let classified = crate::classify_page(PageNumber::new(map_page), raw, &mut budget())?;
    let record = crate::locate_usage_map(
        classified,
        crate::MapRowLocator::new(PageNumber::new(map_page), row),
        &mut budget(),
    )?;
    let map = record.raw();
    assert_eq!(&map[..5], &[0; 5]);
    Ok(map[5 + target as usize / 8] & (1 << (target % 8)) != 0)
}

pub(super) fn page_rows(bytes: &[u8], page: usize) -> u16 {
    let offset = page * crate::PAGE_BYTES + 8;
    u16::from_le_bytes([bytes[offset], bytes[offset + 1]])
}

#[test]
fn exhausted_row_slots_spill_to_the_next_page() -> TestResult {
    let directory = TempDir::new("create")?;
    let table = TableSpec {
        validation: crate::TableValidation::NONE,
        name: b"Bits",
        columns: &[ColumnSpec::new(b"Bit", ColumnType::Boolean)],
        indexes: &[],
    };
    let row = [RowValue::Boolean(true)];
    let rows = vec![row.as_slice(); 257];
    create(directory.target(), &[TableRows { table, rows: &rows }])?;
    let bytes = fs::read(directory.target())?;
    assert_eq!(bytes.len(), 25 * crate::PAGE_BYTES);
    assert_eq!((page_rows(&bytes, 23), page_rows(&bytes, 24)), (255, 2));
    assert!(!map_bit(&bytes, 21, 1, 23)?);
    assert!(map_bit(&bytes, 21, 1, 24)?);
    Ok(())
}

#[test]
fn packed_pages_track_ownership_availability_and_total_rows() -> TestResult {
    let directory = TempDir::new("create")?;
    let table = table(b"Numbers", &[ID], &[]);
    let values = (0..509)
        .map(|value| [RowValue::Long(value)])
        .collect::<Vec<_>>();
    let rows = values.iter().map(|row| row.as_slice()).collect::<Vec<_>>();
    create(directory.target(), &[TableRows { table, rows: &rows }])?;
    let bytes = fs::read(directory.target())?;
    assert_eq!(bytes.len(), 26 * crate::PAGE_BYTES);
    assert_eq!(
        &bytes[20 * crate::PAGE_BYTES + 12..20 * crate::PAGE_BYTES + 16],
        &509_u32.to_le_bytes()
    );
    assert_eq!(
        [
            page_rows(&bytes, 23),
            page_rows(&bytes, 24),
            page_rows(&bytes, 25)
        ],
        [254, 254, 1]
    );
    for page in 0..30 {
        assert_eq!(map_bit(&bytes, 21, 0, page)?, (23..26).contains(&page));
        assert_eq!(map_bit(&bytes, 21, 1, page)?, page == 25);
        assert_eq!(map_bit(&bytes, 1, 0, page)?, page >= 26);
    }
    Ok(())
}

#[test]
fn spilling_a_large_row_keeps_space_for_smaller_rows_available() -> TestResult {
    let directory = TempDir::new("create")?;
    let table = TableSpec {
        validation: crate::TableValidation::NONE,
        name: b"TextRows",
        columns: &[
            ID,
            ColumnSpec::new(b"Text", ColumnType::Text { max_len: nz(255) }),
        ],
        indexes: &[],
    };
    let text = [b'x'; 255];
    let row = [RowValue::Long(1), RowValue::Text(&text)];
    let rows = vec![row.as_slice(); 8];
    create(directory.target(), &[TableRows { table, rows: &rows }])?;
    let bytes = fs::read(directory.target())?;
    assert_eq!(bytes.len(), 25 * crate::PAGE_BYTES);
    assert_eq!((page_rows(&bytes, 23), page_rows(&bytes, 24)), (7, 1));
    assert!(map_bit(&bytes, 21, 1, 23)?);
    assert!(map_bit(&bytes, 21, 1, 24)?);
    Ok(())
}

#[test]
fn later_page_corruption_and_missing_owned_pages_are_detected() -> TestResult {
    let directory = TempDir::new("create")?;
    let table = table(b"Numbers", &[ID], &[]);
    let row = [RowValue::Long(1)];
    let rows = vec![row.as_slice(); 255];
    create(directory.target(), &[TableRows { table, rows: &rows }])?;
    let original = fs::read(directory.target())?;
    let mut changed = original.clone();
    changed[24 * crate::PAGE_BYTES + 4] = 21;
    fs::write(directory.target(), &changed)?;
    assert!(matches!(
        super::check::check_initial_rows(&directory.target(), &table, &rows, &mut budget()),
        Err(ImageCheckError::Rows(_))
    ));
    let mut changed = original;
    let entry = 21 * crate::PAGE_BYTES + 10;
    let start = u16::from_le_bytes([changed[entry], changed[entry + 1]]) as usize;
    changed[21 * crate::PAGE_BYTES + start + 5 + 24 / 8] &= !1;
    fs::write(directory.target(), &changed)?;
    assert!(matches!(
        super::check::check_initial_rows(&directory.target(), &table, &rows, &mut budget()),
        Err(ImageCheckError::Mismatch {
            detail: "initial row count"
        })
    ));
    Ok(())
}

#[test]
fn initial_rows_extend_past_inline_maps() -> TestResult {
    let directory = TempDir::new("create")?;
    let names = (0..70)
        .map(|number| format!("F{number}"))
        .collect::<Vec<_>>();
    let columns = names
        .iter()
        .map(|name| ColumnSpec::new(name.as_bytes(), ColumnType::Double))
        .collect::<Vec<_>>();
    let table = table(b"WideRows", &columns, &[]);
    let row = [RowValue::Double(1.0); 70];
    // Three 570-byte fixed-width rows per page, without wide variable offsets.
    let rows = vec![row.as_slice(); 3004];
    create(
        directory.target(),
        &[TableRows {
            table,
            rows: &rows[..3003],
        }],
    )?;
    let original = fs::read(directory.target())?;
    assert_eq!(original.len(), 1024 * crate::PAGE_BYTES);
    assert!(map_bit(&original, 21, 0, 1023)?);
    assert!(!map_bit(&original, 1, 0, 1023)?);
    let grown = directory.target().with_file_name("grown.mdb");
    create(&grown, &[TableRows { table, rows: &rows }])?;
    assert!(fs::metadata(grown)?.len() > 1024 * crate::PAGE_BYTES as u64);
    Ok(())
}

#[test]
fn oversized_rows_and_page_storage_budget_fail_before_publication() -> TestResult {
    let directory = TempDir::new("create")?;
    let columns = [b"A", b"B", b"C", b"D", b"E", b"F", b"G", b"H", b"I"]
        .map(|name| ColumnSpec::new(name, ColumnType::Text { max_len: nz(255) }));
    let table = table(b"WideRows", &columns, &[]);
    let text = [b'x'; 255];
    let row = [RowValue::Text(&text); 9];
    assert!(matches!(
        create(
            directory.target(),
            &[TableRows {
                table,
                rows: &[&row]
            }]
        ),
        Err(WriteError::Compose(ComposeError::Row(_)))
    ));
    for (limits, kind) in [
        (
            ResourceLimits::default().with_max_allocation_bytes(crate::ByteCount::new(2000)),
            crate::ResourceLimitKind::AllocationBytes,
        ),
        (
            ResourceLimits::default().with_max_total_work_units(0),
            crate::ResourceLimitKind::TotalWorkUnits,
        ),
    ] {
        let result = create_database(
            directory.target(),
            &DatabaseSpec {
                tables: &[TableRows {
                    table: scalar_table(),
                    rows: &[&[RowValue::Long(1), RowValue::Null]],
                }],
                ..DatabaseSpec::default()
            },
            &mut ResourceBudget::new(limits),
        );
        assert!(
            matches!(
                result,
                Err(WriteError::Compose(ComposeError::Encoding(
                    crate::Error::ResourceLimitExceeded { kind: actual, .. }
                ))) if actual == kind
            ),
            "{kind:?}: {result:?}"
        );
    }
    assert!(directory.entries()?.is_empty());
    Ok(())
}
