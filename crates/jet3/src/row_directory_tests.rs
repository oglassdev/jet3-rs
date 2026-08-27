use super::{PAGE_BYTES, RowDirectory, RowDirectoryError};
use crate::{PageNumber, ResourceBudget, ResourceLimits};

fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}

fn page(owner: u32, rows: &[(&[u8], u16)]) -> [u8; PAGE_BYTES] {
    let mut page = [0_u8; PAGE_BYTES];
    page[0] = 1;
    page[4..8].copy_from_slice(&owner.to_le_bytes());
    page[8..10].copy_from_slice(&u16::try_from(rows.len()).unwrap_or_default().to_le_bytes());
    let mut start = PAGE_BYTES;
    for (index, (row, flags)) in rows.iter().enumerate() {
        start -= row.len();
        let raw = u16::try_from(start).unwrap_or_default() | flags;
        page[10 + 2 * index..12 + 2 * index].copy_from_slice(&raw.to_le_bytes());
        page[start..start + row.len()].copy_from_slice(row);
    }
    page
}

#[test]
fn validates_reverse_rows_and_skips_deleted_or_overflow_storage()
-> Result<(), Box<dyn std::error::Error>> {
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
    assert!(directory.next_primary(&page)?.is_none());
    assert_eq!(resources.item_work(), 3);
    Ok(())
}

#[test]
fn admits_only_exact_active_overflow_pointers() -> Result<(), Box<dyn std::error::Error>> {
    let valid = page(7, &[(&[0, 3, 0, 0], 0x4000)]);
    RowDirectory::validate(
        PageNumber::new(3),
        PageNumber::new(7),
        &valid,
        &mut budget(),
    )?;

    let short = page(7, &[(&[0, 3, 0], 0x4000)]);
    assert!(matches!(
        RowDirectory::validate(
            PageNumber::new(3),
            PageNumber::new(7),
            &short,
            &mut budget(),
        ),
        Err(RowDirectoryError::InvalidOverflowPointerLength { length: 3, .. })
    ));
    Ok(())
}

#[test]
fn rejects_owner_flags_offsets_overlap_and_truncation() {
    let valid = page(7, &[(b"a", 0), (b"b", 0)]);
    assert!(matches!(
        RowDirectory::validate(
            PageNumber::new(3),
            PageNumber::new(8),
            &valid,
            &mut budget(),
        ),
        Err(RowDirectoryError::UnexpectedOwner { .. })
    ));

    let mut unknown = valid;
    let raw = u16::from_le_bytes([unknown[10], unknown[11]]) | 0x2000;
    unknown[10..12].copy_from_slice(&raw.to_le_bytes());
    assert!(matches!(
        RowDirectory::validate(
            PageNumber::new(3),
            PageNumber::new(7),
            &unknown,
            &mut budget(),
        ),
        Err(RowDirectoryError::UnknownFlag { .. })
    ));

    let mut overlap = valid;
    let first_offset = u16::from_le_bytes([overlap[10], overlap[11]]);
    overlap[12..14].copy_from_slice(&first_offset.to_le_bytes());
    assert!(matches!(
        RowDirectory::validate(
            PageNumber::new(3),
            PageNumber::new(7),
            &overlap,
            &mut budget(),
        ),
        Err(RowDirectoryError::InvalidBounds { .. })
    ));

    let mut count = valid;
    count[8..10].copy_from_slice(&1020_u16.to_le_bytes());
    assert!(matches!(
        RowDirectory::validate(
            PageNumber::new(3),
            PageNumber::new(7),
            &count,
            &mut budget(),
        ),
        Err(RowDirectoryError::RowCountTooLarge { .. })
    ));
}

#[test]
fn revalidates_a_reloaded_overflow_source_before_resuming() -> Result<(), Box<dyn std::error::Error>>
{
    let original = page(7, &[(b"first", 0), (b"second", 0)]);
    let mut resources = budget();
    let mut previous = RowDirectory::validate(
        PageNumber::new(3),
        PageNumber::new(7),
        &original,
        &mut resources,
    )?;
    let _ = previous.next_primary(&original)?.ok_or("missing row")?;

    let changed = page(7, &[(b"first", 0)]);
    let current = RowDirectory::validate(
        PageNumber::new(3),
        PageNumber::new(7),
        &changed,
        &mut resources,
    )?;
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
