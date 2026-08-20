#![no_main]

// Exact expected mappings are limited to the README Pages table recorded as
// SRC-0020; this target does not inspect or derive any other page-header field.

use std::hint::black_box;

use jet3::{
    JET3_PAGE_SIZE, PageKind, PageNumber, ReadLimits, ResourceBudget, ResourceLimits,
    classify_page,
};
use libfuzzer_sys::fuzz_target;

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const CONTROL_BYTES: usize = 3;

fuzz_target!(|data: &[u8]| {
    let tag = selector(data.first().copied());
    let page_number = u64::from(selector(data.get(1).copied()));
    let work_limit = match selector(data.get(2).copied()) % 3 {
        0 => 0,
        1 => 1,
        _ => 2,
    };
    let payload = data.get(CONTROL_BYTES..).unwrap_or_default();
    let mut page = [0_u8; PAGE_BYTES];
    fill_repeating(&mut page, payload);
    page[0] = tag;

    exercise(page_number, &page, work_limit);
    exercise_fixed_mappings(&page);
});

fn exercise(page_number: u64, page: &[u8; PAGE_BYTES], work_limit: u64) {
    let mut budget = budget(work_limit);
    let first = classify_page(PageNumber::new(page_number), page, &mut budget);
    if work_limit == 0 {
        assert!(first.is_err());
        assert_eq!(budget.total_work_units(), 0);
        return;
    }

    let classified = first.expect("positive work limit permits one classification");
    assert_eq!(classified.number(), PageNumber::new(page_number));
    assert_eq!(classified.raw_bytes(), page);
    assert_eq!(classified.kind(), expected(page_number, page[0]));
    assert_eq!(budget.total_work_units(), 1);

    let second = classify_page(PageNumber::new(page_number), page, &mut budget);
    assert_eq!(second.is_ok(), work_limit == 2);
    assert_eq!(budget.total_work_units(), work_limit);
    let _ = black_box(second);
}

fn exercise_fixed_mappings(template: &[u8; PAGE_BYTES]) {
    let mut budget = budget(12);
    for page_number in [0_u64, 1] {
        for tag in 0_u8..=5 {
            let mut page = *template;
            page[0] = tag;
            let classified = classify_page(PageNumber::new(page_number), &page, &mut budget)
                .expect("fixed mapping budget is exact");
            assert_eq!(classified.kind(), expected(page_number, tag));
        }
    }
    assert_eq!(budget.total_work_units(), 12);
}

fn expected(page_number: u64, tag: u8) -> PageKind {
    if page_number == 0 {
        return if tag == 0 {
            PageKind::DatabaseDefinition
        } else {
            PageKind::Unknown(tag)
        };
    }

    match tag {
        1 => PageKind::Data,
        2 => PageKind::TableDefinition,
        3 => PageKind::IntermediateIndex,
        4 => PageKind::LeafIndex,
        5 => PageKind::ExtendedUsageBitmap,
        _ => PageKind::Unknown(tag),
    }
}

fn budget(work_limit: u64) -> ResourceBudget {
    ResourceBudget::new(
        ResourceLimits::new(ReadLimits::default()).with_max_total_work_units(work_limit),
    )
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
