#![no_main]

use std::hint::black_box;

use jet3::{
    ByteCount, DatabaseReader, JET3_PAGE_SIZE, ReadLimits, ResourceBudget, ResourceLimits,
    SliceSource,
};
use libfuzzer_sys::fuzz_target;

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const CONTROL_BYTES: usize = 5;
const MAX_SOURCE_BYTES: usize = 2 * PAGE_BYTES;
const SIGNATURE_BYTES: u64 = 15;
const COMPLETE_OPEN_BYTES: u64 = SIGNATURE_BYTES + JET3_PAGE_SIZE.get();

fuzz_target!(|data: &[u8]| {
    let payload = data.get(CONTROL_BYTES..).unwrap_or_default();
    let bounded_payload = payload
        .get(..payload.len().min(MAX_SOURCE_BYTES))
        .unwrap_or_default();

    exercise_open(
        bounded_payload,
        selected_limits(data, bounded_payload.len()),
    );

    let mut expanded_page = [0x5a_u8; PAGE_BYTES];
    let copied = bounded_payload.len().min(PAGE_BYTES);
    if let (Some(source), Some(destination)) = (
        bounded_payload.get(..copied),
        expanded_page.get_mut(..copied),
    ) {
        destination.copy_from_slice(source);
    }
    exercise_boundaries(&expanded_page);
});

fn exercise_boundaries(page_bytes: &[u8; PAGE_BYTES]) {
    let page = JET3_PAGE_SIZE.get();
    let policies = [
        limits(page, page, COMPLETE_OPEN_BYTES, 1, 1),
        limits(page - 1, page, COMPLETE_OPEN_BYTES, 1, 1),
        limits(page, SIGNATURE_BYTES - 1, COMPLETE_OPEN_BYTES, 1, 1),
        limits(page, page - 1, COMPLETE_OPEN_BYTES, 1, 1),
        limits(page, page, SIGNATURE_BYTES - 1, 1, 1),
        limits(page, page, COMPLETE_OPEN_BYTES - 1, 1, 1),
        limits(page, page, COMPLETE_OPEN_BYTES, 0, 1),
        limits(page, page, COMPLETE_OPEN_BYTES, 1, 0),
    ];

    for policy in policies {
        exercise_open(page_bytes, policy);
    }
}

fn exercise_open(source_bytes: &[u8], policy: ResourceLimits) {
    let mut budget = ResourceBudget::new(policy);
    let source = match SliceSource::new(source_bytes, budget.read_budget()) {
        Ok(source) => source,
        Err(error) => {
            black_box(error);
            return;
        }
    };

    match DatabaseReader::from_source(source, &mut budget) {
        Ok(database) => {
            black_box(database.signature_kind());
            black_box(database.geometry());
            black_box(database.header().raw_bytes());
            black_box(database.into_source());
        }
        Err(error) => {
            black_box(error);
        }
    }
    black_box(budget.read_budget().total_read());
    black_box(budget.page_visits());
    black_box(budget.total_work_units());
    black_box(budget.allocation_bytes());
}

fn selected_limits(data: &[u8], source_len: usize) -> ResourceLimits {
    let source_len = u64::try_from(source_len).unwrap_or(u64::MAX);
    limits(
        select_input(data.first().copied(), source_len),
        select_read(data.get(1).copied()),
        select_total(data.get(2).copied()),
        select_work(data.get(3).copied()),
        select_work(data.get(4).copied()),
    )
}

fn limits(
    input: u64,
    single_read: u64,
    total_read: u64,
    page_visits: u64,
    total_work: u64,
) -> ResourceLimits {
    ResourceLimits::new(ReadLimits::new(
        ByteCount::new(input),
        ByteCount::new(single_read),
        ByteCount::new(total_read),
    ))
    .with_max_page_visits(page_visits)
    .with_max_total_work_units(total_work)
}

fn select_input(selector: Option<u8>, source_len: u64) -> u64 {
    match selector.unwrap_or_default() % 5 {
        0 => 0,
        1 => source_len.saturating_sub(1),
        2 => source_len,
        3 => source_len.saturating_add(1),
        _ => MAX_SOURCE_BYTES as u64,
    }
}

fn select_read(selector: Option<u8>) -> u64 {
    match selector.unwrap_or_default() % 5 {
        0 => 0,
        1 => SIGNATURE_BYTES - 1,
        2 => SIGNATURE_BYTES,
        3 => JET3_PAGE_SIZE.get() - 1,
        _ => JET3_PAGE_SIZE.get(),
    }
}

fn select_total(selector: Option<u8>) -> u64 {
    match selector.unwrap_or_default() % 6 {
        0 => 0,
        1 => SIGNATURE_BYTES - 1,
        2 => SIGNATURE_BYTES,
        3 => JET3_PAGE_SIZE.get(),
        4 => COMPLETE_OPEN_BYTES - 1,
        _ => COMPLETE_OPEN_BYTES,
    }
}

fn select_work(selector: Option<u8>) -> u64 {
    match selector.unwrap_or_default() % 3 {
        0 => 0,
        1 => 1,
        _ => u64::MAX,
    }
}
