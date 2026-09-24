use super::directory::{RowDirectory, RowDirectoryError};
use super::reader_tests::write_rows;
use crate::{PAGE_BYTES, PageNumber};

use crate::testkit::{TestResult, budget};

fn page(owner: u32, rows: &[(&[u8], u16)]) -> [u8; PAGE_BYTES] {
    let mut page = [0_u8; PAGE_BYTES];
    write_rows(&mut page, owner, rows);
    page
}

fn validate(page: &[u8; PAGE_BYTES], owner: u64) -> Result<RowDirectory, RowDirectoryError> {
    RowDirectory::validate(
        PageNumber::new(3),
        PageNumber::new(owner),
        page,
        &mut budget(),
    )
}

#[test]
fn validates_reverse_rows_and_skips_deleted_or_overflow_storage() -> TestResult {
    // EXP-0060: deleting the first-packed row leaves a zero-length 0xc000
    // slot whose masked start equals the page end.
    let page_end_tombstone = page(7, &[(b"", 0xc000), (b"first", 0)]);
    let mut resources = budget();
    let mut directory = RowDirectory::validate(
        PageNumber::new(9),
        PageNumber::new(7),
        &page_end_tombstone,
        &mut resources,
    )?;
    let first = directory
        .next_primary(&page_end_tombstone)?
        .ok_or("missing surviving row")?;
    assert_eq!(&page_end_tombstone[first.range()], b"first");
    assert_eq!(first.locator().slot(), 1);
    assert!(directory.next_primary(&page_end_tombstone)?.is_none());

    let page = page(7, &[(b"first", 0), (b"", 0xc000), (b"target", 0x8000)]);
    let mut resources = budget();
    let mut directory = RowDirectory::validate(
        PageNumber::new(9),
        PageNumber::new(7),
        &page,
        &mut resources,
    )?;
    let first = directory.next_primary(&page)?.ok_or("missing first row")?;
    assert_eq!(&page[first.range()], b"first");
    assert_eq!(first.locator().page(), PageNumber::new(9));
    assert_eq!(first.locator().slot(), 0);
    assert!(!first.overflow());
    assert!(!first.hidden());
    assert!(directory.next_primary(&page)?.is_none());
    assert_eq!(resources.item_work(), 3);
    Ok(())
}

#[test]
fn direct_entry_access_reports_flags_and_missing_rows() -> TestResult {
    let page = page(7, &[(b"first", 0), (b"target", 0xc000)]);
    let directory = validate(&page, 7)?;
    let target = directory.entry(&page, 1)?;
    assert!(target.overflow());
    assert!(target.hidden());
    assert_eq!(&page[target.range()], b"target");
    assert!(matches!(
        directory.entry(&page, 2),
        Err(RowDirectoryError::MissingRow { .. })
    ));
    Ok(())
}

#[test]
fn rejects_owner_flags_offsets_overlap_truncation_and_short_pointers() -> TestResult {
    validate(&page(7, &[(&[0, 3, 0, 0], 0x4000)]), 7)?;
    assert!(matches!(
        validate(&page(7, &[(&[0, 3, 0], 0x4000)]), 7),
        Err(RowDirectoryError::InvalidOverflowPointerLength { length: 3, .. })
    ));

    let valid = page(7, &[(b"a", 0), (b"b", 0)]);
    assert!(matches!(
        validate(&valid, 8),
        Err(RowDirectoryError::UnexpectedOwner { .. })
    ));
    let first_offset = u16::from_le_bytes([valid[10], valid[11]]);
    let mut unknown = valid;
    unknown[10..12].copy_from_slice(&(first_offset | 0x2000).to_le_bytes());
    assert!(matches!(
        validate(&unknown, 7),
        Err(RowDirectoryError::UnknownFlag { .. })
    ));
    let mut overlap = valid;
    overlap[12..14].copy_from_slice(&first_offset.to_le_bytes());
    assert!(matches!(
        validate(&overlap, 7),
        Err(RowDirectoryError::InvalidBounds { .. })
    ));
    for raw in [2048_u16, 0xc801] {
        let mut out_of_page = valid;
        out_of_page[10..12].copy_from_slice(&raw.to_le_bytes());
        assert!(matches!(
            validate(&out_of_page, 7),
            Err(RowDirectoryError::OffsetOutOfPage { .. })
        ));
    }
    let mut count = valid;
    count[8..10].copy_from_slice(&1020_u16.to_le_bytes());
    assert!(matches!(
        validate(&count, 7),
        Err(RowDirectoryError::RowCountTooLarge { .. })
    ));
    Ok(())
}

#[test]
fn revalidates_a_reloaded_overflow_source_before_resuming() -> TestResult {
    let original = page(7, &[(b"first", 0), (b"second", 0)]);
    let mut previous = validate(&original, 7)?;
    previous.next_primary(&original)?.ok_or("missing row")?;
    let current = validate(&page(7, &[(b"first", 0)]), 7)?;
    assert!(matches!(
        current.resume_after(&previous),
        Err(RowDirectoryError::DirectoryChanged {
            previous_row_count: 2,
            current_row_count: 1,
            ..
        })
    ));
    Ok(())
}
