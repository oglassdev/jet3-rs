#![no_main]

use std::hint::black_box;

use jet3::{
    ByteCount, JET3_PAGE_SIZE, Jet3PageReader, PageNumber, ReadLimits, ResourceBudget,
    ResourceLimits, SliceSource,
};
use libfuzzer_sys::fuzz_target;

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const CONTROL_BYTES: usize = 5;
const COMMAND_BYTES: usize = 9;
const MAX_COMMANDS: usize = 64;
const MAX_READ_ATTEMPTS: usize = 2 * MAX_COMMANDS;
const MAX_SOURCE_BYTES: usize = 2 * PAGE_BYTES;
const ONE_BELOW_PAGE: u64 = JET3_PAGE_SIZE.get() - 1;
const MAX_TOTAL_READ_BYTES: u64 = (MAX_READ_ATTEMPTS as u64) * JET3_PAGE_SIZE.get();
const SYNTHETIC_PARTIAL: [u8; 1] = [0x5a];
const SYNTHETIC_PAGE: [u8; PAGE_BYTES] = [0xa5; PAGE_BYTES];

fuzz_target!(|data: &[u8]| {
    let payload = match data.get(CONTROL_BYTES..) {
        Some(bytes) => bytes,
        None => &[],
    };
    let bounded_len = payload.len().min(MAX_SOURCE_BYTES);
    let bounded_payload = match payload.get(..bounded_len) {
        Some(bytes) => bytes,
        None => return,
    };
    let aligned_len = bounded_len - (bounded_len % PAGE_BYTES);
    let aligned_payload = match bounded_payload.get(..aligned_len) {
        Some(bytes) => bytes,
        None => return,
    };

    exercise_constructor(&[]);
    exercise_constructor(&SYNTHETIC_PARTIAL);
    exercise_constructor(&SYNTHETIC_PAGE);
    exercise_constructor(bounded_payload);

    exercise_selected_policy(data, payload, aligned_payload);
    exercise_boundary_policies();
});

fn exercise_constructor(input: &[u8]) {
    let input_len = usize_to_u64(input.len());
    let limits = limits(input_len, JET3_PAGE_SIZE.get(), JET3_PAGE_SIZE.get(), 1, 1);
    let mut budget = ResourceBudget::new(limits);
    let source = SliceSource::new(input, budget.read_budget());
    let reader = source.and_then(Jet3PageReader::new);
    let _ = black_box(reader);
}

fn exercise_selected_policy(data: &[u8], commands: &[u8], source_bytes: &[u8]) {
    let source_len = usize_to_u64(source_bytes.len());
    let selected = limits(
        select_input_limit(data.first().copied(), source_len),
        select_single_read_limit(data.get(1).copied()),
        select_total_read_limit(data.get(2).copied(), source_len),
        select_visit_limit(data.get(3).copied()),
        select_visit_limit(data.get(4).copied()),
    );
    let mut budget = ResourceBudget::new(selected);
    let source = SliceSource::new(source_bytes, budget.read_budget());
    let mut reader = match source.and_then(Jet3PageReader::new) {
        Ok(reader) => reader,
        Err(error) => {
            black_box(error);
            return;
        }
    };
    let page_count = reader.geometry().page_count();
    let mut last_page = PageNumber::new(0);
    let mut destination = [0_u8; PAGE_BYTES];

    for command in commands.chunks(COMMAND_BYTES).take(MAX_COMMANDS) {
        let Some((&opcode, operand)) = command.split_first() else {
            continue;
        };
        let page = select_page(opcode, operand, page_count, last_page);
        let result = reader.read_page(page, &mut destination, &mut budget);
        let _ = black_box(result);
        black_box(&destination);

        if opcode % 6 == 4 {
            let repeated = reader.read_page(page, &mut destination, &mut budget);
            let _ = black_box(repeated);
            black_box(&destination);
        }
        last_page = page;
    }

    black_box(budget.read_budget().total_read());
    black_box(budget.page_visits());
    black_box(budget.total_work_units());
}

fn exercise_boundary_policies() {
    let page = JET3_PAGE_SIZE.get();
    let scenarios = [
        limits(page, page, page, 1, 1),
        limits(page, ONE_BELOW_PAGE, page, 1, 1),
        limits(page, page, ONE_BELOW_PAGE, 1, 1),
        limits(page, page, page, 0, 1),
        limits(page, page, page, 1, 0),
    ];

    for policy in scenarios {
        exercise_one_read(policy);
    }
}

fn exercise_one_read(policy: ResourceLimits) {
    let mut budget = ResourceBudget::new(policy);
    let source = SliceSource::new(&SYNTHETIC_PAGE, budget.read_budget());
    let mut reader = match source.and_then(Jet3PageReader::new) {
        Ok(reader) => reader,
        Err(error) => {
            black_box(error);
            return;
        }
    };
    let mut destination = [0_u8; PAGE_BYTES];
    let result = reader.read_page(PageNumber::new(0), &mut destination, &mut budget);
    let _ = black_box(result);
    black_box(destination);
    black_box(budget.read_budget().total_read());
    black_box(budget.page_visits());
    black_box(budget.total_work_units());
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

fn select_input_limit(selector: Option<u8>, source_len: u64) -> u64 {
    match selector.map_or(0, |byte| byte) % 6 {
        0 => 0,
        1 => source_len.saturating_sub(1),
        2 => source_len,
        3 => source_len.saturating_add(1),
        4 => JET3_PAGE_SIZE.get(),
        _ => MAX_SOURCE_BYTES as u64,
    }
}

fn select_single_read_limit(selector: Option<u8>) -> u64 {
    match selector.map_or(0, |byte| byte) % 5 {
        0 => 0,
        1 => ONE_BELOW_PAGE,
        2 => JET3_PAGE_SIZE.get(),
        3 => JET3_PAGE_SIZE.get() + 1,
        _ => MAX_SOURCE_BYTES as u64,
    }
}

fn select_total_read_limit(selector: Option<u8>, source_len: u64) -> u64 {
    match selector.map_or(0, |byte| byte) % 7 {
        0 => 0,
        1 => ONE_BELOW_PAGE,
        2 => JET3_PAGE_SIZE.get(),
        3 => MAX_SOURCE_BYTES as u64,
        4 => source_len,
        5 => MAX_TOTAL_READ_BYTES,
        _ => MAX_TOTAL_READ_BYTES + JET3_PAGE_SIZE.get(),
    }
}

fn select_visit_limit(selector: Option<u8>) -> u64 {
    match selector.map_or(0, |byte| byte) % 6 {
        0 => 0,
        1 => 1,
        2 => 2,
        3 => (MAX_COMMANDS - 1) as u64,
        4 => MAX_COMMANDS as u64,
        _ => (2 * MAX_COMMANDS) as u64,
    }
}

fn select_page(opcode: u8, operand: &[u8], page_count: u64, last_page: PageNumber) -> PageNumber {
    match opcode % 6 {
        0 => PageNumber::new(decode_u64(operand)),
        1 => PageNumber::new(0),
        2 => PageNumber::new(page_count.saturating_sub(1)),
        3 => PageNumber::new(page_count),
        4 => last_page,
        _ => PageNumber::new(u64::MAX),
    }
}

fn decode_u64(bytes: &[u8]) -> u64 {
    bytes
        .iter()
        .copied()
        .take(8)
        .enumerate()
        .fold(0_u64, |value, (index, byte)| {
            value | (u64::from(byte) << (index * 8))
        })
}

fn usize_to_u64(value: usize) -> u64 {
    u64::try_from(value).map_or(u64::MAX, |converted| converted)
}
