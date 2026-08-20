#![no_main]

// Layout assertions are limited to the detached allocation-map facts recorded
// as SRC-0020. All bytes constructed here are project-authored synthetic data.

use std::hint::black_box;

use jet3::{
    AllocationMap, AllocationMapError, ByteCount, JET3_PAGE_SIZE, PageGeometry, PageNumber,
    ReadLimits, ResourceBudget, ResourceLimits, classify_page, decode_allocation_map,
    extended_allocation_bits,
};
use libfuzzer_sys::fuzz_target;

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const EXTENDED_BITMAP_OFFSET: usize = 4;
const EXTENDED_BITS: u64 = ((PAGE_BYTES - EXTENDED_BITMAP_OFFSET) * 8) as u64;
const CONTROL_BYTES: usize = 2;
const MAX_SELECTED_WORK: u64 = 64;
const MAX_CURSOR_CALLS: usize = 65;

fuzz_target!(|data: &[u8]| {
    let mode = selector(data.first().copied()) % 4;
    let work_limit = u64::from(selector(data.get(1).copied())) % (MAX_SELECTED_WORK + 1);
    let payload = data.get(CONTROL_BYTES..).unwrap_or_default();

    match mode {
        0 => exercise_record(payload, work_limit),
        1 => {
            let mut record = [0_u8; 69];
            fill_repeating(&mut record[1..], payload);
            exercise_record(&record, work_limit);
        }
        2 => {
            let mut record = [0_u8; 65];
            fill_repeating(&mut record[1..], payload);
            record[0] = 1;
            exercise_record(&record, work_limit);
        }
        _ => exercise_extended(payload, work_limit),
    }

    exercise_decode_boundaries();
    exercise_inline_boundaries();
    exercise_indirect_references();
    exercise_extended_boundaries();
});

fn exercise_record(record: &[u8], work_limit: u64) {
    let mut budget = budget(work_limit);
    let Ok(map) = decode_allocation_map(record, &mut budget) else {
        assert_eq!(budget.item_work(), 0);
        assert!(budget.total_work_units() <= work_limit);
        return;
    };
    assert_eq!(budget.total_work_units(), 1);

    match map {
        AllocationMap::Inline(inline) => {
            assert!(inline.start_page().get() <= u64::from(u32::MAX));
            let geometry = PageGeometry::new(ByteCount::new(u64::MAX), ByteCount::new(1))
                .expect("one-byte geometry spans every representable source byte");
            let mut pages = inline.allocated_pages(geometry);
            for _ in 0..MAX_CURSOR_CALLS {
                match pages.next_page(&mut budget) {
                    Ok(Some(page)) => assert!(page.get() < geometry.page_count()),
                    Ok(None) | Err(_) => break,
                }
            }
        }
        AllocationMap::Indirect(indirect) => {
            assert_eq!(indirect.reference_count(), record.len().saturating_sub(1) / 4);
            let mut references = indirect.map_page_references();
            for _ in 0..MAX_CURSOR_CALLS {
                match references.next_reference(&mut budget) {
                    Ok(Some(reference)) => {
                        let _ = black_box(reference);
                    }
                    Ok(None) | Err(_) => break,
                }
            }
        }
        _ => unreachable!("all allocation-map variants are handled"),
    }

    assert!(budget.item_work() <= work_limit);
    assert_eq!(budget.item_work() + 1, budget.total_work_units());
}

fn exercise_extended(payload: &[u8], work_limit: u64) {
    let mut page = [0_u8; PAGE_BYTES];
    fill_repeating(&mut page, payload);
    page[0] = 5;

    let mut classification_budget = budget(1);
    let classified = classify_page(PageNumber::new(1), &page, &mut classification_budget)
        .expect("one work unit permits the synthetic page classification");
    let mut bits = extended_allocation_bits(classified)
        .expect("tag five on a nonzero page is an extended bitmap classification");
    let mut scan_budget = budget(work_limit);
    for _ in 0..MAX_CURSOR_CALLS {
        match bits.next_bit(&mut scan_budget) {
            Ok(Some(bit)) => assert!(bit < EXTENDED_BITS),
            Ok(None) | Err(_) => break,
        }
    }
    assert!(scan_budget.item_work() <= work_limit);
    assert_eq!(scan_budget.item_work(), scan_budget.total_work_units());
}

fn exercise_decode_boundaries() {
    let mut rejected = budget(0);
    assert!(matches!(
        decode_allocation_map(&[0xfe], &mut rejected),
        Err(AllocationMapError::Resource(_))
    ));
    assert_eq!(rejected.total_work_units(), 0);

    assert_eq!(decode(&[]), Err(AllocationMapError::EmptyRecord));
    for actual_len in 1..5 {
        let record = [0_u8; 4];
        assert_eq!(
            decode(&record[..actual_len]),
            Err(AllocationMapError::InlineRecordTooShort { actual_len })
        );
    }
    assert_eq!(
        decode(&[0xfe]),
        Err(AllocationMapError::UnsupportedRecordType { record_type: 0xfe })
    );
    for payload_len in 1..4 {
        let record = [1_u8, 0, 0, 0];
        assert_eq!(
            decode(&record[..=payload_len]),
            Err(AllocationMapError::IndirectPayloadMisaligned { payload_len })
        );
    }
}

fn exercise_inline_boundaries() {
    let start_record = [0, 0x78, 0x56, 0x34, 0x12];
    let AllocationMap::Inline(start_only) =
        decode(&start_record).expect("little-endian start-only inline record is valid")
    else {
        unreachable!("tag zero decodes as inline");
    };
    assert_eq!(start_only.start_page(), PageNumber::new(0x1234_5678));

    let record = [0, 0, 0, 0, 0, 0x81, 0x81];
    let AllocationMap::Inline(inline) =
        decode(&record).expect("fixed inline record is valid")
    else {
        unreachable!("tag zero decodes as inline");
    };
    let geometry = PageGeometry::new(ByteCount::new(16), ByteCount::new(1))
        .expect("fixed sixteen-page geometry is valid");
    let mut pages = inline.allocated_pages(geometry);
    let mut exact = budget(16);
    for expected in [0, 7, 8, 15] {
        assert_eq!(
            pages.next_page(&mut exact),
            Ok(Some(PageNumber::new(expected)))
        );
    }
    assert_eq!(pages.next_page(&mut exact), Ok(None));
    assert_eq!(exact.item_work(), 16);
    assert_eq!(exact.total_work_units(), 16);

    let tight_record = [0, 0, 0, 0, 0, 0x80];
    let AllocationMap::Inline(inline) =
        decode(&tight_record).expect("fixed tight inline record is valid")
    else {
        unreachable!("tag zero decodes as inline");
    };
    let mut pages = inline.allocated_pages(geometry);
    let mut one_under = budget(7);
    assert!(matches!(
        pages.next_page(&mut one_under),
        Err(AllocationMapError::Resource(_))
    ));
    assert_eq!(one_under.item_work(), 7);

    let mut replacement = budget(1);
    assert_eq!(
        pages.next_page(&mut replacement),
        Ok(Some(PageNumber::new(7)))
    );
    assert_eq!(replacement.item_work(), 1);

    let zero_past_end = [0, 16, 0, 0, 0, 0];
    let AllocationMap::Inline(inline) =
        decode(&zero_past_end).expect("unset out-of-geometry bit is syntactically valid")
    else {
        unreachable!("tag zero decodes as inline");
    };
    let mut pages = inline.allocated_pages(geometry);
    let mut exact = budget(8);
    assert_eq!(pages.next_page(&mut exact), Ok(None));

    let set_past_end = [0, 16, 0, 0, 0, 1];
    let AllocationMap::Inline(inline) =
        decode(&set_past_end).expect("set out-of-geometry bit decodes before iteration")
    else {
        unreachable!("tag zero decodes as inline");
    };
    let mut pages = inline.allocated_pages(geometry);
    let mut exact = budget(1);
    assert!(matches!(
        pages.next_page(&mut exact),
        Err(AllocationMapError::PageReference(_))
    ));
}

fn exercise_indirect_references() {
    let record = [
        1, 0, 0, 0, 0, 0x78, 0x56, 0x34, 0x12, 0xff, 0xff, 0xff, 0xff,
    ];
    let AllocationMap::Indirect(indirect) =
        decode(&record).expect("fixed indirect record is valid")
    else {
        unreachable!("tag one decodes as indirect");
    };
    assert_eq!(indirect.reference_count(), 3);
    let mut references = indirect.map_page_references();
    let mut tight = budget(2);
    for expected in [0, 0x1234_5678] {
        assert_eq!(references.next_reference(&mut tight), Ok(Some(expected)));
    }
    assert!(matches!(
        references.next_reference(&mut tight),
        Err(AllocationMapError::Resource(_))
    ));
    assert_eq!(tight.item_work(), 2);

    let mut replacement = budget(1);
    assert_eq!(
        references.next_reference(&mut replacement),
        Ok(Some(u32::MAX))
    );
    assert_eq!(references.next_reference(&mut replacement), Ok(None));
}

fn exercise_extended_boundaries() {
    let mut page = [0_u8; PAGE_BYTES];
    page[0] = 5;
    page[1..4].copy_from_slice(&[0xff, 0xaa, 0x55]);
    page[4] = 0x81;
    page[5] = 0x01;
    page[PAGE_BYTES - 1] = 0x80;

    let mut classification_budget = budget(1);
    let classified = classify_page(PageNumber::new(1), &page, &mut classification_budget)
        .expect("fixed page classification budget is exact");
    let mut bits = extended_allocation_bits(classified)
        .expect("unknown extended-header bytes are not validity requirements");
    let mut one_under = budget(EXTENDED_BITS - 1);
    for expected in [0, 7, 8] {
        assert_eq!(bits.next_bit(&mut one_under), Ok(Some(expected)));
    }
    assert!(matches!(
        bits.next_bit(&mut one_under),
        Err(AllocationMapError::Resource(_))
    ));
    assert_eq!(one_under.item_work(), EXTENDED_BITS - 1);

    let mut replacement = budget(1);
    assert_eq!(bits.next_bit(&mut replacement), Ok(Some(EXTENDED_BITS - 1)));
    assert_eq!(bits.next_bit(&mut replacement), Ok(None));
    assert_eq!(replacement.item_work(), 1);
}

fn budget(work_limit: u64) -> ResourceBudget {
    ResourceBudget::new(
        ResourceLimits::new(ReadLimits::default())
            .with_max_item_work(work_limit)
            .with_max_total_work_units(work_limit),
    )
}

fn decode(record: &[u8]) -> Result<AllocationMap<'_>, AllocationMapError> {
    let mut resources = budget(1);
    let result = decode_allocation_map(record, &mut resources);
    assert_eq!(resources.item_work(), 0);
    assert_eq!(resources.total_work_units(), 1);
    result
}

fn selector(value: Option<u8>) -> u8 {
    let value = value.unwrap_or_default();
    if value.is_ascii_digit() {
        value - b'0'
    } else {
        value
    }
}

fn fill_repeating(destination: &mut [u8], source: &[u8]) {
    if source.is_empty() {
        return;
    }
    for (index, byte) in destination.iter_mut().enumerate() {
        *byte = source[index % source.len()];
    }
}
