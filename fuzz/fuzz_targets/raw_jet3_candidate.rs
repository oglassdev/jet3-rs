#![no_main]

use std::hint::black_box;

use jet3::{
    ByteCount, JET3_PAGE_SIZE, PageNumber, RawJet3Candidate, ReadLimits, ResourceBudget,
    ResourceLimits, SliceSource,
};
use libfuzzer_sys::fuzz_target;

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const CONTROL_BYTES: usize = 6;
const MAX_SOURCE_BYTES: usize = 2 * PAGE_BYTES;
const SIGNATURE_READ_BYTES: u64 = 15;
const ONE_BELOW_SIGNATURE: u64 = SIGNATURE_READ_BYTES - 1;
const ONE_BELOW_PAGE: u64 = JET3_PAGE_SIZE.get() - 1;
const COMPLETE_OPERATION_BYTES: u64 = SIGNATURE_READ_BYTES + JET3_PAGE_SIZE.get();

fuzz_target!(|data: &[u8]| {
    let payload = data.get(CONTROL_BYTES..).unwrap_or_default();
    let bounded_payload = payload
        .get(..payload.len().min(MAX_SOURCE_BYTES))
        .unwrap_or_default();

    exercise_selected_policy(data, bounded_payload);

    let mut expanded_page = [0x5a_u8; PAGE_BYTES];
    let copied = bounded_payload.len().min(PAGE_BYTES);
    if let (Some(source), Some(destination)) = (
        bounded_payload.get(..copied),
        expanded_page.get_mut(..copied),
    ) {
        destination.copy_from_slice(source);
    }
    exercise_boundary_policies(&expanded_page, data.get(5).copied());
});

fn exercise_selected_policy(data: &[u8], source_bytes: &[u8]) {
    let source_len = usize_to_u64(source_bytes.len());
    let limits = resource_limits(
        select_input_limit(data.first().copied(), source_len),
        select_single_read_limit(data.get(1).copied()),
        select_total_read_limit(data.get(2).copied()),
        select_work_limit(data.get(3).copied()),
        select_work_limit(data.get(4).copied()),
    );
    exercise_candidate(source_bytes, data.get(5).copied(), limits);
}

fn exercise_boundary_policies(source_bytes: &[u8; PAGE_BYTES], page_selector: Option<u8>) {
    let page = JET3_PAGE_SIZE.get();
    let scenarios = [
        resource_limits(page, page, COMPLETE_OPERATION_BYTES, 1, 1),
        resource_limits(page - 1, page, COMPLETE_OPERATION_BYTES, 1, 1),
        resource_limits(page, ONE_BELOW_SIGNATURE, COMPLETE_OPERATION_BYTES, 1, 1),
        resource_limits(page, page, ONE_BELOW_SIGNATURE, 1, 1),
        resource_limits(page, page, COMPLETE_OPERATION_BYTES - 1, 1, 1),
        resource_limits(page, page, COMPLETE_OPERATION_BYTES, 0, 1),
        resource_limits(page, page, COMPLETE_OPERATION_BYTES, 1, 0),
    ];

    for limits in scenarios {
        exercise_candidate(source_bytes, page_selector, limits);
    }
}

fn exercise_candidate(source_bytes: &[u8], page_selector: Option<u8>, limits: ResourceLimits) {
    let mut budget = ResourceBudget::new(limits);
    let source = match SliceSource::new(source_bytes, budget.read_budget()) {
        Ok(source) => source,
        Err(error) => {
            black_box(error);
            return;
        }
    };
    let mut candidate = match RawJet3Candidate::inspect(source, &mut budget) {
        Ok(candidate) => candidate,
        Err(error) => {
            black_box(error);
            return;
        }
    };

    black_box(candidate.signature_kind());
    let geometry = candidate.geometry();
    black_box(geometry);
    let page = select_page(page_selector, geometry.page_count());
    let mut destination = [0_u8; PAGE_BYTES];
    let result = candidate.read_raw_page(page, &mut destination, &mut budget);
    let _ = black_box(result);
    black_box(destination);
    black_box(budget.read_budget().total_read());
    black_box(budget.page_visits());
    black_box(budget.total_work_units());
}

fn resource_limits(
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

fn select_input_limit(selector: Option<u8>, source_len: u64) -> u64 {
    match selector.unwrap_or_default() % 6 {
        0 => 0,
        1 => source_len.saturating_sub(1),
        2 => source_len,
        3 => source_len.saturating_add(1),
        4 => JET3_PAGE_SIZE.get(),
        _ => MAX_SOURCE_BYTES as u64,
    }
}

fn select_single_read_limit(selector: Option<u8>) -> u64 {
    match selector.unwrap_or_default() % 6 {
        0 => 0,
        1 => ONE_BELOW_SIGNATURE,
        2 => SIGNATURE_READ_BYTES,
        3 => ONE_BELOW_PAGE,
        4 => JET3_PAGE_SIZE.get(),
        _ => MAX_SOURCE_BYTES as u64,
    }
}

fn select_total_read_limit(selector: Option<u8>) -> u64 {
    match selector.unwrap_or_default() % 7 {
        0 => 0,
        1 => ONE_BELOW_SIGNATURE,
        2 => SIGNATURE_READ_BYTES,
        3 => ONE_BELOW_PAGE,
        4 => JET3_PAGE_SIZE.get(),
        5 => COMPLETE_OPERATION_BYTES,
        _ => 2 * COMPLETE_OPERATION_BYTES,
    }
}

fn select_work_limit(selector: Option<u8>) -> u64 {
    match selector.unwrap_or_default() % 4 {
        0 => 0,
        1 => 1,
        2 => 2,
        _ => u64::MAX,
    }
}

fn select_page(selector: Option<u8>, page_count: u64) -> PageNumber {
    match selector.unwrap_or_default() % 4 {
        0 => PageNumber::new(0),
        1 => PageNumber::new(page_count.saturating_sub(1)),
        2 => PageNumber::new(page_count),
        _ => PageNumber::new(u64::MAX),
    }
}

fn usize_to_u64(value: usize) -> u64 {
    u64::try_from(value).unwrap_or(u64::MAX)
}
