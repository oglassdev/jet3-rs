use super::insert_indexed_tests::*;
use crate::{DatabaseReader, IndexKind, PAGE_BYTES, RowValue};
use std::fs;

#[test]
fn released_page_corruption_refuses_without_publication() -> TestResult {
    let f = Fixture::new(6, false, IndexKind::Primary)?;
    let row = f.rows()?[0].1;
    for (_, row) in f.rows()? {
        f.delete(row)?;
    }
    let released = fs::read(f.path())?;
    let offset = row.page().get() as usize * PAGE_BYTES;
    for field in [2, 10, 11] {
        let mut damaged = released.clone();
        damaged[offset + field] ^= 1;
        fs::write(f.path(), &damaged)?;
        assert!(f.insert(10, 20).is_err());
        assert_eq!(fs::read(f.path())?, damaged);
    }
    // A free page belonging to another object is not an allocation candidate.
    let mut foreign = released.clone();
    foreign[offset + 4..offset + 8].copy_from_slice(&2_u32.to_le_bytes());
    fs::write(f.path(), &foreign)?;
    let new = f.insert(10, 20)?;
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
    f.delete(first)?;
    let mut db = DatabaseReader::open(f.path(), &mut b)?;
    assert!(crate::alloc::patch::available(
        &mut db,
        &table,
        first.page(),
        &mut b
    )?);
    drop(db);
    let before = fs::metadata(f.path())?.len();
    let new = f.insert(0, 77)?;
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

#[test]
fn indexed_rows_release_last_live_slot_and_reinsert() -> TestResult {
    for keep in [0, 2, 5] {
        let f = Fixture::new(6, false, IndexKind::Primary)?;
        for id in [4, 1, 5, 0, 3, 2].into_iter().filter(|id| *id != keep) {
            let row = f.rows()?.into_iter().find(|r| r.0 == id).ok_or("row")?.1;
            f.delete(row)?;
        }
        let row = f.rows()?[0].1;
        let before = fs::read(f.path())?;
        f.delete(row)?;
        let after = fs::read(f.path())?;
        let mut expected = *page(&before, row.page())?;
        expected[0] = 9;
        expected[2..4].copy_from_slice(&2026_u16.to_le_bytes());
        for slot in 0..6 {
            expected[10 + slot * 2..12 + slot * 2].copy_from_slice(&0xc800_u16.to_le_bytes());
        }
        assert_eq!(page(&after, row.page())?, &expected);
        assert!(f.rows()?.is_empty());
        f.validate()?;
        let new = f.insert(-1, 5)?;
        assert_eq!(new.page(), row.page());
        assert_eq!(fs::metadata(f.path())?.len() as usize, after.len());
        assert_eq!(f.rows()?, vec![(-1, new)]);
        expected[0] = 1;
        expected[8..10].copy_from_slice(&1_u16.to_le_bytes());
        expected[10..12].copy_from_slice(&2038_u16.to_le_bytes());
        expected[2038..].copy_from_slice(&[2, 255, 255, 255, 255, 5, 0, 0, 0, 3]);
        assert_eq!(page(&fs::read(f.path())?, row.page())?, &expected);
        f.validate()?;
    }
    Ok(())
}
