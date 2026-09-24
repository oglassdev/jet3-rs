use super::usage_map::{UsageMapError, locate_usage_map};
use crate::{
    MapRowLocator, PAGE_BYTES, PageKind, PageNumber, ReadLimits, ResourceBudget, ResourceLimits,
    classify_page,
};
use std::ops::Range;

use crate::testkit::{TestResult, budget};

fn data_page(entries: &[u16]) -> [u8; PAGE_BYTES] {
    let mut page = [0_u8; PAGE_BYTES];
    page[0] = 1;
    page[8..10].copy_from_slice(&(entries.len() as u16).to_le_bytes());
    for (slot, entry) in entries.iter().enumerate() {
        page[10 + slot * 2..12 + slot * 2].copy_from_slice(&entry.to_le_bytes());
    }
    page
}

/// Locates map row `row` on `raw`, classified as page 1.
fn locate(
    raw: &[u8; PAGE_BYTES],
    row: u8,
    resources: &mut ResourceBudget,
) -> TestResult<Result<Range<usize>, UsageMapError>> {
    let page = classify_page(PageNumber::new(1), raw, resources)?;
    Ok(
        locate_usage_map(page, MapRowLocator::new(PageNumber::new(1), row), resources)
            .map(|map| map.range()),
    )
}

#[test]
fn row_zero_ends_at_page_end_and_later_rows_end_at_prior_start() -> TestResult {
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
    assert_eq!(
        row_zero.location(),
        MapRowLocator::new(PageNumber::new(3), 0)
    );
    assert_eq!(row_zero.range(), 2000..2048);
    assert!(row_zero.raw().iter().all(|byte| *byte == 0xaa));
    let row_one = locate_usage_map(
        page,
        MapRowLocator::new(PageNumber::new(3), 1),
        &mut resources,
    )?;
    assert_eq!(row_one.range(), 1900..2000);
    assert!(row_one.raw().iter().all(|byte| *byte == 0xbb));
    // The directory end is the exact lower bound for a row start.
    assert_eq!(locate(&data_page(&[12]), 0, &mut budget())?, Ok(12..2048));
    Ok(())
}

#[test]
fn rejects_missing_flagged_and_out_of_directory_rows() -> TestResult {
    let mut oversized = data_page(&[]);
    oversized[8..10].copy_from_slice(&1020_u16.to_le_bytes());
    let mut cases = vec![
        (
            data_page(&[]),
            UsageMapError::RowOutOfBounds {
                row: 0,
                row_count: 0,
            },
        ),
        (
            oversized,
            UsageMapError::RowCountTooLarge {
                row_count: 1020,
                maximum: 1019,
            },
        ),
    ];
    for raw_offset in [0x800c_u16, 0x400c, 0x200c, 0x100c] {
        cases.push((
            data_page(&[raw_offset]),
            UsageMapError::FlaggedOrOutOfPageRow { row: 0, raw_offset },
        ));
    }
    for (raw, expected) in cases {
        assert_eq!(locate(&raw, 0, &mut budget())?, Err(expected));
    }
    assert!(matches!(
        locate(&data_page(&[11]), 0, &mut budget())?,
        Err(UsageMapError::InvalidRowBounds { .. })
    ));
    let mut limited = ResourceBudget::new(
        ResourceLimits::new(ReadLimits::default()).with_max_total_work_units(1),
    );
    assert!(matches!(
        locate(&data_page(&[12]), 0, &mut limited)?,
        Err(UsageMapError::Resource(_))
    ));
    Ok(())
}

#[test]
fn requires_matching_data_page() -> TestResult {
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

#[test]
fn unrelated_deleted_map_slots_keep_neighbor_bounds_and_selected_slots_are_refused() -> TestResult {
    // EXP-0297: middle and final native deleted map slots keep their prior start.
    for entries in [
        vec![2000, 0xc000 | 2000, 1900],
        vec![2000, 1900, 0xc000 | 1900],
    ] {
        let raw = data_page(&entries);
        for (row, entry) in entries.iter().enumerate() {
            let result = locate(&raw, row as u8, &mut budget())?;
            if entry & 0xc000 != 0 {
                assert!(matches!(
                    result,
                    Err(UsageMapError::FlaggedOrOutOfPageRow { .. })
                ));
            } else {
                let expected = if row == 0 { 2000..2048 } else { 1900..2000 };
                assert_eq!(result, Ok(expected));
            }
        }
    }
    assert_eq!(
        locate(&data_page(&[0xc800, 2000]), 1, &mut budget())?,
        Ok(2000..2048)
    );
    for entry in [
        0x8000 | 2000,
        0x4000 | 2000,
        0xe000 | 2000,
        0xc000 | 2049,
        0xc000 | 1900,
        0xc000 | 11,
    ] {
        let result = locate(&data_page(&[2000, entry]), 0, &mut budget())?;
        assert!(result.is_err(), "{entry:#x}");
    }
    Ok(())
}
