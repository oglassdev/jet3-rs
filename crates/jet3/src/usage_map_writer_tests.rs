use super::{
    EXTENDED_BITMAP_BITS, ExtendedUsageMapEncoder, InlineUsageMapEncoder, UsageMapWriteError,
    encode_indirect_references, indirect_record_len,
};
use crate::limits::ReadLimits;
use crate::{
    AllocationMap, ByteCount, Error, JET3_PAGE_SIZE, PageGeometry, PageNumber, ReachedMapPage,
    ResourceBudget, ResourceLimits, classify_page, decode_allocation_map,
};

type TestResult = Result<(), Box<dyn std::error::Error>>;

fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}

fn out_of_map(page: u64, first: u64, page_count: u64) -> UsageMapWriteError {
    UsageMapWriteError::PageOutOfMap {
        page: PageNumber::new(page),
        first: PageNumber::new(first),
        page_count,
    }
}

#[test]
fn inline_record_round_trips_through_decoder() -> TestResult {
    let mut budget = budget();
    let mut encoder =
        InlineUsageMapEncoder::new(PageNumber::new(100), ByteCount::new(2), &mut budget)?;
    for page in [100, 107, 115] {
        encoder.set_page(PageNumber::new(page))?;
    }
    encoder.clear_page(PageNumber::new(107))?;
    assert_eq!(encoder.is_set(PageNumber::new(115)), Ok(true));
    assert_eq!(encoder.is_set(PageNumber::new(107)), Ok(false));

    let mut record = [0xee; 8];
    assert_eq!(
        encoder.encode_into(&mut record, &mut budget)?,
        ByteCount::new(7)
    );
    assert_eq!(record, [0x00, 100, 0, 0, 0, 0b0000_0001, 0b1000_0000, 0xee]);

    let geometry = PageGeometry::new(ByteCount::new(200 * JET3_PAGE_SIZE.get()), JET3_PAGE_SIZE)?;
    let AllocationMap::Inline(map) = decode_allocation_map(&record[..7], &mut budget)? else {
        return Err("expected inline map".into());
    };
    let mut pages = map.allocated_pages(geometry);
    let mut decoded = Vec::new();
    while let Some(page) = pages.next_page(&mut budget)? {
        decoded.push(page.get());
    }
    assert_eq!(decoded, [100, 115]);
    Ok(())
}

#[test]
fn inline_map_rejects_pages_outside_its_range() -> TestResult {
    let mut encoder =
        InlineUsageMapEncoder::new(PageNumber::new(100), ByteCount::new(2), &mut budget())?;
    assert_eq!(
        encoder.set_page(PageNumber::new(99)),
        Err(out_of_map(99, 100, 16))
    );
    assert_eq!(
        encoder.set_page(PageNumber::new(116)),
        Err(out_of_map(116, 100, 16))
    );
    assert_eq!(
        InlineUsageMapEncoder::new(
            PageNumber::new(u64::from(u32::MAX) + 1),
            ByteCount::new(1),
            &mut budget()
        )
        .err(),
        Some(UsageMapWriteError::PageNotRepresentable {
            page: PageNumber::new(u64::from(u32::MAX) + 1),
        })
    );
    Ok(())
}

#[test]
fn inline_encoding_rejects_short_output_and_exhausted_budgets() -> TestResult {
    let allocation_limited =
        ResourceLimits::new(ReadLimits::default()).with_max_allocation_bytes(ByteCount::new(1));
    assert!(matches!(
        InlineUsageMapEncoder::new(
            PageNumber::new(0),
            ByteCount::new(2),
            &mut ResourceBudget::new(allocation_limited)
        ),
        Err(UsageMapWriteError::Encoding(
            Error::ResourceLimitExceeded { .. }
        ))
    ));

    let reserve_limited = ResourceLimits::new(ReadLimits::default())
        .with_max_allocation_bytes(ByteCount::new(u64::MAX))
        .with_max_total_work_units(u64::MAX);
    assert!(matches!(
        InlineUsageMapEncoder::new(
            PageNumber::new(0),
            ByteCount::from_usize(usize::MAX)?,
            &mut ResourceBudget::new(reserve_limited)
        ),
        Err(UsageMapWriteError::Encoding(Error::Io {
            kind: std::io::ErrorKind::OutOfMemory,
            ..
        }))
    ));

    let encoder = InlineUsageMapEncoder::new(PageNumber::new(0), ByteCount::new(2), &mut budget())?;
    assert!(matches!(
        encoder.encode_into(&mut [0; 6], &mut budget()),
        Err(UsageMapWriteError::Encoding(
            Error::OutputCapacityExceeded { .. }
        ))
    ));
    let encoded_limited =
        ResourceLimits::new(ReadLimits::default()).with_max_encoded_bytes(ByteCount::new(6));
    assert!(matches!(
        encoder.encode_into(&mut [0; 7], &mut ResourceBudget::new(encoded_limited)),
        Err(UsageMapWriteError::Encoding(
            Error::ResourceLimitExceeded { .. }
        ))
    ));
    Ok(())
}

#[test]
fn extended_page_round_trips_through_traversal_decoder() -> TestResult {
    let mut budget = budget();
    let mut encoder = ExtendedUsageMapEncoder::new(1, &mut budget)?;
    assert_eq!(encoder.first_page(), PageNumber::new(EXTENDED_BITMAP_BITS));
    let first = EXTENDED_BITMAP_BITS;
    let last = 2 * EXTENDED_BITMAP_BITS - 1;
    encoder.set_page(PageNumber::new(first), &mut budget)?;
    encoder.set_page(PageNumber::new(first + 9), &mut budget)?;
    encoder.set_page(PageNumber::new(last), &mut budget)?;
    encoder.clear_page(PageNumber::new(first + 9), &mut budget)?;
    assert_eq!(
        encoder.set_page(PageNumber::new(first - 1), &mut budget),
        Err(out_of_map(first - 1, first, EXTENDED_BITMAP_BITS))
    );
    assert_eq!(
        encoder.set_page(PageNumber::new(last + 1), &mut budget),
        Err(out_of_map(last + 1, first, EXTENDED_BITMAP_BITS))
    );

    let image = encoder.into_image();
    assert_eq!(&image.as_bytes()[..4], &[0x05, 0x01, 0x00, 0x00]);
    let classified = classify_page(PageNumber::new(7041), image.as_bytes(), &mut budget)?;
    let mut reached = ReachedMapPage::new(1, classified)?;
    let mut pages = Vec::new();
    while let Some(bit) = reached.relative_bits().next_bit(&mut budget)? {
        pages.push(reached.absolute_page(bit)?.get());
    }
    assert_eq!(pages, [first, last]);
    Ok(())
}

#[test]
fn extended_slot_overflow_is_structured() {
    assert!(matches!(
        ExtendedUsageMapEncoder::new(u64::MAX, &mut budget()),
        Err(UsageMapWriteError::Encoding(Error::Arithmetic { .. }))
    ));
}

#[test]
fn extended_updates_are_budgeted_and_atomic() -> TestResult {
    let encoded_limited =
        ResourceLimits::new(ReadLimits::default()).with_max_encoded_bytes(ByteCount::new(4));
    let mut budget = ResourceBudget::new(encoded_limited);
    let mut encoder = ExtendedUsageMapEncoder::new(0, &mut budget)?;
    let before = *encoder.image().as_bytes();

    assert!(matches!(
        encoder.set_page(PageNumber::new(0), &mut budget),
        Err(UsageMapWriteError::Encoding(
            Error::ResourceLimitExceeded { .. }
        ))
    ));
    assert_eq!(encoder.image().as_bytes(), &before);
    Ok(())
}

#[test]
fn indirect_row_round_trips_and_rejects_wide_references() -> TestResult {
    let mut budget = budget();
    let references = [
        PageNumber::new(21),
        PageNumber::new(0),
        PageNumber::new(7041),
    ];
    let mut row = [0; 13];
    assert_eq!(
        encode_indirect_references(&references, &mut row, &mut budget)?,
        indirect_record_len(3)?
    );
    let AllocationMap::Indirect(map) = decode_allocation_map(&row, &mut budget)? else {
        return Err("expected indirect map".into());
    };
    let mut cursor = map.map_page_references();
    let mut decoded = Vec::new();
    while let Some(reference) = cursor.next_reference(&mut budget)? {
        decoded.push(u64::from(reference));
    }
    assert_eq!(decoded, [21, 0, 7041]);

    assert_eq!(
        encode_indirect_references(
            &[PageNumber::new(u64::from(u32::MAX) + 1)],
            &mut [0; 5],
            &mut budget
        ),
        Err(UsageMapWriteError::PageNotRepresentable {
            page: PageNumber::new(u64::from(u32::MAX) + 1),
        })
    );
    assert!(matches!(
        encode_indirect_references(&references, &mut [0; 12], &mut budget),
        Err(UsageMapWriteError::Encoding(
            Error::OutputCapacityExceeded { .. }
        ))
    ));
    Ok(())
}
