use super::*;
use crate::allocation::AllocationMapLayout;
use crate::mutation_map::BitSpan;
use crate::{MapRowLocator, ResourceLimits};

type TestResult = Result<(), Box<dyn std::error::Error>>;
fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}
fn inline(first: u64, bytes: &[u8]) -> MapBits {
    let mut row = vec![0; 5 + bytes.len()];
    row[1..5].copy_from_slice(&(first as u32).to_le_bytes());
    row[5..].copy_from_slice(bytes);
    MapBits {
        locator: MapRowLocator::new(PageNumber::new(21), 0),
        range: 100..100 + row.len(),
        layout: AllocationMapLayout::Inline {
            start_page: PageNumber::new(first),
            bitmap: 5..row.len(),
        },
        row,
        spans: vec![BitSpan {
            first,
            page: PageNumber::new(21),
            offset: 105,
            bytes: bytes.to_vec(),
        }],
    }
}

#[test]
fn shifted_inline_conversion_preserves_both_slot_edges_and_zero_holes() -> TestResult {
    let mut map = inline(16344, &[0x80, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]);
    let mut edits = PageEdits::new(40000);
    map.set(
        PageNumber::new(32704),
        true,
        false,
        40000,
        &mut edits,
        &mut budget(),
    )?;
    for page in [16351, 16352, 32704] {
        assert!(map.contains(PageNumber::new(page))?);
    }
    for page in [0, 16344, 16353, 32703] {
        assert!(!map.contains(PageNumber::new(page))?);
    }
    assert_eq!(edits.next_append_page()?.get(), 40003);
    assert_eq!(
        map.spans.iter().map(|span| span.first).collect::<Vec<_>>(),
        [0, 16352, 32704]
    );
    Ok(())
}

#[test]
fn empty_availability_window_does_not_allocate_for_a_clear_bit() -> TestResult {
    let mut map = inline(16352, &[0; 128]);
    let mut edits = PageEdits::new(20000);
    map.set(
        PageNumber::new(12000),
        false,
        false,
        20000,
        &mut edits,
        &mut budget(),
    )?;
    assert_eq!(edits.next_append_page()?.get(), 20000);
    assert!(matches!(map.layout, AllocationMapLayout::Inline { .. }));
    map.set(
        PageNumber::new(12000),
        true,
        false,
        20000,
        &mut edits,
        &mut budget(),
    )?;
    assert!(map.contains(PageNumber::new(12000))?);
    assert!(!map.contains(PageNumber::new(u64::MAX))?);
    assert_eq!(edits.next_append_page()?.get(), 20001);
    Ok(())
}

#[test]
fn reference_capacity_and_bitmap_budget_fail_before_publication() -> TestResult {
    let mut map = inline(0, &[0; 4]);
    let mut edits = PageEdits::new(30);
    assert!(
        map.set(
            PageNumber::new(33 * 16352),
            true,
            false,
            30,
            &mut edits,
            &mut budget()
        )
        .is_err()
    );
    assert_eq!(edits.next_append_page()?.get(), 30);
    let mut map = inline(0, &[0; 128]);
    let mut b = ResourceBudget::new(
        ResourceLimits::default().with_max_allocation_bytes(crate::ByteCount::new(0)),
    );
    assert!(
        map.set(PageNumber::new(1024), true, false, 30, &mut edits, &mut b)
            .is_err()
    );
    assert_eq!(edits.next_append_page()?.get(), 30);
    Ok(())
}
