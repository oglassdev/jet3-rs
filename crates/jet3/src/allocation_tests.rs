use super::{AllocationMap, AllocationMapError, decode_allocation_map, extended_allocation_bits};
use crate::{
    ByteCount, Error, JET3_PAGE_SIZE, PageGeometry, PageNumber, ReadLimits, ResourceBudget,
    ResourceLimitKind, ResourceLimits, classify_page,
};

type TestResult = Result<(), Box<dyn std::error::Error>>;

fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}

fn budget_with_items(maximum: u64) -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::new(ReadLimits::default()).with_max_item_work(maximum))
}

fn budget_with_work(maximum: u64) -> ResourceBudget {
    ResourceBudget::new(
        ResourceLimits::new(ReadLimits::default()).with_max_total_work_units(maximum),
    )
}

fn geometry(page_count: u64) -> Result<PageGeometry, Error> {
    let source_len = page_count
        .checked_mul(JET3_PAGE_SIZE.get())
        .ok_or(Error::Arithmetic {
            operation: "construct allocation test geometry",
        })?;
    PageGeometry::new(ByteCount::new(source_len), JET3_PAGE_SIZE)
}

#[test]
fn record_shape_errors_are_structured_and_charge_one_work_unit() {
    let mut resources = budget();
    assert_eq!(
        decode_allocation_map(&[], &mut resources),
        Err(AllocationMapError::EmptyRecord)
    );
    assert_eq!(resources.total_work_units(), 1);
    for actual_len in 1..5 {
        let mut resources = budget();
        assert_eq!(
            decode_allocation_map(&[0; 4][..actual_len], &mut resources),
            Err(AllocationMapError::InlineRecordTooShort { actual_len })
        );
        assert_eq!(resources.total_work_units(), 1);
    }
    let mut resources = budget();
    assert_eq!(
        decode_allocation_map(&[1, 0, 0, 0], &mut resources),
        Err(AllocationMapError::IndirectPayloadMisaligned { payload_len: 3 })
    );
    assert_eq!(resources.total_work_units(), 1);
    let mut resources = budget();
    assert_eq!(
        decode_allocation_map(&[2], &mut resources),
        Err(AllocationMapError::UnsupportedRecordType { record_type: 2 })
    );
    assert_eq!(resources.total_work_units(), 1);
}

#[test]
fn record_decode_resource_failure_charges_nothing() {
    let mut resources = budget_with_work(0);
    assert_eq!(
        decode_allocation_map(&[0, 0, 0, 0, 0], &mut resources),
        Err(AllocationMapError::Resource(Error::ResourceLimitExceeded {
            kind: ResourceLimitKind::TotalWorkUnits,
            requested: 1,
            maximum: 0,
        }))
    );
    assert_eq!(resources.item_work(), 0);
    assert_eq!(resources.total_work_units(), 0);
}

#[test]
fn inline_bits_are_lsb_first_and_charge_clear_bits() -> TestResult {
    let record = [0, 10, 0, 0, 0, 0b1000_0010];
    let mut resources = budget();
    let AllocationMap::Inline(map) = decode_allocation_map(&record, &mut resources)? else {
        return Err("expected inline map".into());
    };
    assert_eq!(map.start_page(), PageNumber::new(10));

    let mut pages = map.allocated_pages(geometry(32)?);
    assert_eq!(pages.next_page(&mut resources)?, Some(PageNumber::new(11)));
    assert_eq!(resources.item_work(), 2);
    assert_eq!(resources.total_work_units(), 3);
    assert_eq!(pages.next_page(&mut resources)?, Some(PageNumber::new(17)));
    assert_eq!(resources.item_work(), 8);
    assert_eq!(resources.total_work_units(), 9);
    assert_eq!(pages.next_page(&mut resources)?, None);
    Ok(())
}

#[test]
fn unset_inline_bits_beyond_geometry_are_not_validated() -> TestResult {
    let record = [0, 0, 0, 0, 0, 0b0000_0001, 0];
    let mut resources = budget();
    let AllocationMap::Inline(map) = decode_allocation_map(&record, &mut resources)? else {
        return Err("expected inline map".into());
    };
    let mut pages = map.allocated_pages(geometry(1)?);

    assert_eq!(pages.next_page(&mut resources)?, Some(PageNumber::new(0)));
    assert_eq!(pages.next_page(&mut resources)?, None);
    assert_eq!(resources.item_work(), 16);
    Ok(())
}

#[test]
fn set_inline_page_outside_geometry_is_rejected_and_exhausts_cursor() -> TestResult {
    let record = [0, 0, 0, 0, 0, 0b0000_0010];
    let mut resources = budget();
    let AllocationMap::Inline(map) = decode_allocation_map(&record, &mut resources)? else {
        return Err("expected inline map".into());
    };
    let mut pages = map.allocated_pages(geometry(1)?);

    assert_eq!(
        pages.next_page(&mut resources),
        Err(AllocationMapError::PageReference(Error::PageOutOfBounds {
            page: 1,
            page_count: 1,
        }))
    );
    assert_eq!(resources.item_work(), 2);
    assert_eq!(pages.next_page(&mut resources), Ok(None));
    Ok(())
}

#[test]
fn inline_resource_failure_does_not_inspect_or_advance_rejected_bit() -> TestResult {
    let record = [0, 20, 0, 0, 0, 0b0000_0100];
    let mut decode_resources = budget();
    let AllocationMap::Inline(map) = decode_allocation_map(&record, &mut decode_resources)? else {
        return Err("expected inline map".into());
    };
    let mut pages = map.allocated_pages(geometry(32)?);
    let mut constrained = budget_with_items(2);

    assert_eq!(
        pages.next_page(&mut constrained),
        Err(AllocationMapError::Resource(Error::ResourceLimitExceeded {
            kind: ResourceLimitKind::ItemWork,
            requested: 3,
            maximum: 2,
        }))
    );
    assert_eq!(constrained.item_work(), 2);
    assert_eq!(constrained.total_work_units(), 2);

    let mut replacement = budget();
    assert_eq!(
        pages.next_page(&mut replacement)?,
        Some(PageNumber::new(22))
    );
    assert_eq!(replacement.item_work(), 1);
    Ok(())
}

#[test]
fn indirect_references_preserve_zero_duplicates_and_boundaries() -> TestResult {
    let record = [
        1, 0, 0, 0, 0, 7, 0, 0, 0, 7, 0, 0, 0, 0xff, 0xff, 0xff, 0xff,
    ];
    let mut resources = budget();
    let AllocationMap::Indirect(map) = decode_allocation_map(&record, &mut resources)? else {
        return Err("expected indirect map".into());
    };
    assert_eq!(map.reference_count(), 4);

    let mut references = map.map_page_references();
    for expected in [0, 7, 7, u32::MAX] {
        assert_eq!(references.next_reference(&mut resources)?, Some(expected));
    }
    assert_eq!(references.next_reference(&mut resources)?, None);
    assert_eq!(references.remaining_references(), 0);
    assert_eq!(resources.item_work(), 4);
    assert_eq!(resources.total_work_units(), 5);
    Ok(())
}

#[test]
fn empty_indirect_payload_is_valid_and_charges_nothing() -> TestResult {
    let mut resources = budget_with_items(0);
    let AllocationMap::Indirect(map) = decode_allocation_map(&[1], &mut resources)? else {
        return Err("expected indirect map".into());
    };
    let mut references = map.map_page_references();
    assert_eq!(references.next_reference(&mut resources)?, None);
    assert_eq!(resources.item_work(), 0);
    Ok(())
}

#[test]
fn indirect_resource_failure_preserves_the_pending_raw_reference() -> TestResult {
    let mut decode_resources = budget();
    let AllocationMap::Indirect(map) =
        decode_allocation_map(&[1, 42, 0, 0, 0], &mut decode_resources)?
    else {
        return Err("expected indirect map".into());
    };
    let mut references = map.map_page_references();
    let mut constrained = budget_with_items(0);
    assert!(matches!(
        references.next_reference(&mut constrained),
        Err(AllocationMapError::Resource(Error::ResourceLimitExceeded {
            kind: ResourceLimitKind::ItemWork,
            requested: 1,
            maximum: 0,
        }))
    ));
    assert_eq!(references.remaining_references(), 1);

    let mut replacement = budget();
    assert_eq!(references.next_reference(&mut replacement)?, Some(42));
    Ok(())
}

#[test]
fn extended_bitmap_ignores_unknown_header_bytes_and_yields_relative_indices() -> TestResult {
    let mut raw = [0; JET3_PAGE_SIZE.get() as usize];
    raw[0] = 0x05;
    raw[1..4].copy_from_slice(&[0xff, 0x7e, 0xa5]);
    raw[4] = 0b0000_0001;
    let last = raw.len() - 1;
    raw[last] = 0b1000_0000;

    let mut resources = budget();
    let page = classify_page(PageNumber::new(9), &raw, &mut resources)?;
    let mut bits = extended_allocation_bits(page)?;
    assert_eq!(bits.next_bit(&mut resources)?, Some(0));
    assert_eq!(bits.next_bit(&mut resources)?, Some(16_351));
    assert_eq!(bits.next_bit(&mut resources)?, None);
    assert_eq!(resources.item_work(), 16_352);
    assert_eq!(resources.total_work_units(), 16_353);
    Ok(())
}

#[test]
fn extended_bitmap_requires_the_classified_page_kind() -> TestResult {
    let mut raw = [0; JET3_PAGE_SIZE.get() as usize];
    raw[0] = 0x01;
    let mut resources = budget();
    let page = classify_page(PageNumber::new(1), &raw, &mut resources)?;

    assert!(matches!(
        extended_allocation_bits(page),
        Err(AllocationMapError::ExpectedExtendedUsageBitmap {
            page,
            actual: crate::PageKind::Data,
        }) if page == PageNumber::new(1)
    ));
    assert_eq!(resources.item_work(), 0);
    assert_eq!(resources.total_work_units(), 1);
    Ok(())
}

#[test]
fn extended_resource_failure_preserves_the_rejected_bit() -> TestResult {
    let mut raw = [0; JET3_PAGE_SIZE.get() as usize];
    raw[0] = 0x05;
    raw[4] = 0b0000_0010;
    let mut classification_budget = budget();
    let page = classify_page(PageNumber::new(1), &raw, &mut classification_budget)?;
    let mut bits = extended_allocation_bits(page)?;
    let mut constrained = budget_with_items(1);

    assert!(matches!(
        bits.next_bit(&mut constrained),
        Err(AllocationMapError::Resource(Error::ResourceLimitExceeded {
            kind: ResourceLimitKind::ItemWork,
            requested: 2,
            maximum: 1,
        }))
    ));
    let mut replacement = budget();
    assert_eq!(bits.next_bit(&mut replacement)?, Some(1));
    assert_eq!(replacement.item_work(), 1);
    Ok(())
}
