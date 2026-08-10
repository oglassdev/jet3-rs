#![no_main]

use std::hint::black_box;

use jet3::{
    ByteCount, ReadBudget, ReadLimits, SliceSource, jet3_page_geometry, read_jet_signature,
};
use libfuzzer_sys::fuzz_target;

const CONTROL_BYTES: usize = 3;
const SIGNATURE_READ_BYTES: u64 = 15;
const ONE_BELOW_SIGNATURE_READ: u64 = SIGNATURE_READ_BYTES - 1;
const MAX_DERIVED_LIMIT: u64 = 4_096;
const MAX_SCENARIOS: usize = 4;

fuzz_target!(|data: &[u8]| {
    let payload = match data.get(CONTROL_BYTES..) {
        Some(payload) => payload,
        None => &[],
    };
    let payload_len = payload.len() as u64;

    let selected = ReadLimits::new(
        ByteCount::new(select_input_limit(data.first().copied(), payload_len)),
        ByteCount::new(select_read_limit(data.get(1).copied(), payload_len)),
        ByteCount::new(select_read_limit(data.get(2).copied(), payload_len)),
    );
    let exact = ReadLimits::new(
        ByteCount::new(payload_len),
        ByteCount::new(SIGNATURE_READ_BYTES),
        ByteCount::new(SIGNATURE_READ_BYTES),
    );
    let one_below_single = ReadLimits::new(
        ByteCount::new(payload_len),
        ByteCount::new(ONE_BELOW_SIGNATURE_READ),
        ByteCount::new(SIGNATURE_READ_BYTES),
    );
    let one_below_total = ReadLimits::new(
        ByteCount::new(payload_len),
        ByteCount::new(SIGNATURE_READ_BYTES),
        ByteCount::new(ONE_BELOW_SIGNATURE_READ),
    );

    for limits in [selected, exact, one_below_single, one_below_total]
        .into_iter()
        .take(MAX_SCENARIOS)
    {
        exercise_header(payload, limits);
    }
});

fn exercise_header(payload: &[u8], limits: ReadLimits) {
    let mut budget = ReadBudget::new(limits);
    let source = black_box(SliceSource::new(payload, &budget));
    let mut source = match source {
        Ok(source) => source,
        Err(error) => {
            black_box(error);
            return;
        }
    };

    let _ = black_box(jet3_page_geometry(&source));
    let _ = black_box(read_jet_signature(&mut source, &mut budget));
    black_box(budget.total_read());
}

fn select_input_limit(selector: Option<u8>, input_len: u64) -> u64 {
    let selector = selector.map_or(0, |value| value);
    match selector % 8 {
        0 => 0,
        1 => input_len.saturating_sub(1),
        2 => input_len,
        3 => input_len.saturating_add(1).min(MAX_DERIVED_LIMIT),
        4 => 2_048,
        5 => 2_049,
        6 => input_len.saturating_mul(2).min(MAX_DERIVED_LIMIT),
        _ => MAX_DERIVED_LIMIT,
    }
}

fn select_read_limit(selector: Option<u8>, input_len: u64) -> u64 {
    let selector = selector.map_or(0, |value| value);
    match selector % 6 {
        0 => 0,
        1 => ONE_BELOW_SIGNATURE_READ,
        2 => SIGNATURE_READ_BYTES,
        3 => SIGNATURE_READ_BYTES + 1,
        4 => input_len.min(MAX_DERIVED_LIMIT),
        _ => MAX_DERIVED_LIMIT,
    }
}
