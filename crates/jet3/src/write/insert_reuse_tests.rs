use super::insert_indexed_tests::*;
use crate::{DatabaseReader, IndexKind, PAGE_BYTES, RowDelete, RowValue, write::insert::*};
use std::fs;

#[test]
fn released_page_corruption_refuses_without_publication() -> TestResult {
    let f = Fixture::new(6, false, IndexKind::Primary)?;
    let row = f.rows()?[0].1;
    for (_, row) in f.rows()? {
        crate::delete_row(
            f.path(),
            RowDelete {
                table: b"Rows",
                row,
            },
            &mut budget(),
        )?;
    }
    let released = fs::read(f.path())?;
    let offset = row.page().get() as usize * PAGE_BYTES;
    for field in [2, 10, 11] {
        let mut damaged = released.clone();
        damaged[offset + field] ^= 1;
        fs::write(f.path(), &damaged)?;
        assert!(
            insert_row(
                f.path(),
                b"Rows",
                &[RowValue::Long(10), RowValue::Long(20)],
                &mut budget()
            )
            .is_err()
        );
        assert_eq!(fs::read(f.path())?, damaged);
    }
    // A free page belonging to another object is not an allocation candidate.
    let mut foreign = released.clone();
    foreign[offset + 4..offset + 8].copy_from_slice(&2_u32.to_le_bytes());
    fs::write(f.path(), &foreign)?;
    let new = insert_row(
        f.path(),
        b"Rows",
        &[RowValue::Long(10), RowValue::Long(20)],
        &mut budget(),
    )?;
    assert_eq!(new.page().get() as usize * PAGE_BYTES, foreign.len());
    assert_eq!(
        page(&fs::read(f.path())?, row.page())?,
        page(&foreign, row.page())?
    );
    f.validate()
}

#[test]
fn dense_page_deletion_and_replacement_update_availability() -> TestResult {
    let f = Fixture::new(201, false, IndexKind::Primary)?;
    let table = f.definition()?;
    let rows = f.rows()?;
    let first = rows[0].1;
    let mut b = budget();
    let mut db = DatabaseReader::open(f.path(), &mut b)?;
    assert!(!crate::alloc::patch::available(
        &mut db,
        &table,
        first.page(),
        &mut b
    )?);
    drop(db);
    crate::update_row(
        f.path(),
        crate::RowUpdate {
            table: b"Rows",
            row: first,
            values: &[RowValue::Long(0), RowValue::Long(77)],
        },
        &mut budget(),
    )?;
    crate::delete_row(
        f.path(),
        RowDelete {
            table: b"Rows",
            row: first,
        },
        &mut budget(),
    )?;
    let mut db = DatabaseReader::open(f.path(), &mut b)?;
    assert!(crate::alloc::patch::available(
        &mut db,
        &table,
        first.page(),
        &mut b
    )?);
    drop(db);
    let before = fs::metadata(f.path())?.len();
    let new = insert_row(
        f.path(),
        b"Rows",
        &[RowValue::Long(0), RowValue::Long(77)],
        &mut budget(),
    )?;
    assert_eq!(new.page(), first.page());
    assert_eq!(fs::metadata(f.path())?.len(), before);
    let mut db = DatabaseReader::open(f.path(), &mut b)?;
    assert!(!crate::alloc::patch::available(
        &mut db,
        &table,
        first.page(),
        &mut b
    )?);
    f.validate()
}
