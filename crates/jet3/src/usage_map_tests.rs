use super::{UsageMapError, locate_usage_map};
use crate::{
    MapRowLocator, PageKind, PageNumber, ReadLimits, ResourceBudget, ResourceLimits, classify_page,
};

const PAGE_BYTES: usize = 2048;

fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::new(ReadLimits::default()))
}

fn data_page(entries: &[u16]) -> [u8; PAGE_BYTES] {
    let mut page = [0_u8; PAGE_BYTES];
    page[0] = 1;
    page[8..10].copy_from_slice(&(entries.len() as u16).to_le_bytes());
    for (slot, entry) in entries.iter().enumerate() {
        page[10 + slot * 2..12 + slot * 2].copy_from_slice(&entry.to_le_bytes());
    }
    page
}

#[test]
fn row_zero_ends_at_page_end_and_later_rows_end_at_prior_start()
-> Result<(), Box<dyn std::error::Error>> {
    let mut raw = data_page(&[2000, 1900]);
    raw[2000..2048].fill(0xaa);
    raw[1900..2000].fill(0xbb);
    let mut resources = budget();
    let page = classify_page(PageNumber::new(3), &raw, &mut resources)?;
    let row_zero = locate_usage_map(
        page,
        MapRowLocator::new(PageNumber::new(3), 0),
        &mut resources,
    )?;
    assert_eq!(row_zero.range(), 2000..2048);
    assert!(row_zero.raw().iter().all(|byte| *byte == 0xaa));
    let row_one = locate_usage_map(
        page,
        MapRowLocator::new(PageNumber::new(3), 1),
        &mut resources,
    )?;
    assert_eq!(row_one.range(), 1900..2000);
    assert!(row_one.raw().iter().all(|byte| *byte == 0xbb));
    Ok(())
}

#[test]
fn zero_rows_rejects_slot_zero() -> Result<(), Box<dyn std::error::Error>> {
    let raw = data_page(&[]);
    let mut resources = budget();
    let page = classify_page(PageNumber::new(1), &raw, &mut resources)?;
    assert_eq!(
        locate_usage_map(
            page,
            MapRowLocator::new(PageNumber::new(1), 0),
            &mut resources,
        ),
        Err(UsageMapError::RowOutOfBounds {
            row: 0,
            row_count: 0
        })
    );
    Ok(())
}

#[test]
fn exact_directory_boundary_is_valid_and_one_below_is_rejected()
-> Result<(), Box<dyn std::error::Error>> {
    for (start, valid) in [(12_u16, true), (11, false)] {
        let raw = data_page(&[start]);
        let mut resources = budget();
        let page = classify_page(PageNumber::new(1), &raw, &mut resources)?;
        let result = locate_usage_map(
            page,
            MapRowLocator::new(PageNumber::new(1), 0),
            &mut resources,
        );
        assert_eq!(result.is_ok(), valid);
    }
    Ok(())
}

#[test]
fn rejects_deleted_overflow_and_other_high_bits() -> Result<(), Box<dyn std::error::Error>> {
    for raw_offset in [0x800c_u16, 0x400c, 0x200c, 0x100c] {
        let raw = data_page(&[raw_offset]);
        let mut resources = budget();
        let page = classify_page(PageNumber::new(1), &raw, &mut resources)?;
        assert_eq!(
            locate_usage_map(
                page,
                MapRowLocator::new(PageNumber::new(1), 0),
                &mut resources,
            ),
            Err(UsageMapError::FlaggedOrOutOfPageRow { row: 0, raw_offset })
        );
    }
    Ok(())
}

#[test]
fn requires_matching_data_page() -> Result<(), Box<dyn std::error::Error>> {
    let mut raw = data_page(&[12]);
    raw[0] = 2;
    let mut resources = budget();
    let page = classify_page(PageNumber::new(2), &raw, &mut resources)?;
    assert!(matches!(
        locate_usage_map(
            page,
            MapRowLocator::new(PageNumber::new(2), 0),
            &mut resources,
        ),
        Err(UsageMapError::ExpectedDataPage {
            actual: PageKind::TableDefinition,
            ..
        })
    ));
    assert!(matches!(
        locate_usage_map(
            page,
            MapRowLocator::new(PageNumber::new(3), 0),
            &mut resources,
        ),
        Err(UsageMapError::PageMismatch { .. })
    ));
    Ok(())
}
