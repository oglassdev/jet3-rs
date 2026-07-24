#![no_main]

use std::hint::black_box;

use jet3::{
    ByteCount, COMMIT_REGION_LENGTH, COMMIT_REGION_OFFSET, COMMIT_SLOT_COUNT, CommitRegion,
    CommitSlotRole, ReadBudget, ReadLimits, SliceSource, read_commit_region,
    read_commit_region_into,
};
use libfuzzer_sys::fuzz_target;

const COMPLETE_SOURCE_BYTES: usize =
    (COMMIT_REGION_OFFSET.get() + COMMIT_REGION_LENGTH.get()) as usize;
const REGION_BYTES: usize = COMMIT_REGION_LENGTH.get() as usize;
const CONTROL_BYTES: usize = 3;
const ONE_BELOW_REGION: u64 = COMMIT_REGION_LENGTH.get() - 1;

fuzz_target!(|data: &[u8]| {
    let payload = data.get(CONTROL_BYTES..).unwrap_or_default();
    let bounded_payload = payload
        .get(..payload.len().min(COMPLETE_SOURCE_BYTES))
        .unwrap_or_default();

    exercise_selected_policy(data, bounded_payload);
    exercise_direct_snapshot(bounded_payload);

    let expanded_page = expanded_page(bounded_payload);
    exercise_boundary_policies(&expanded_page);
});

fn exercise_selected_policy(data: &[u8], source_bytes: &[u8]) {
    let source_len = usize_to_u64(source_bytes.len());
    let limits = ReadLimits::new(
        ByteCount::new(select_input_limit(data.first().copied(), source_len)),
        ByteCount::new(select_read_limit(data.get(1).copied(), source_len)),
        ByteCount::new(select_read_limit(data.get(2).copied(), source_len)),
    );
    exercise_reader(source_bytes, limits);
}

fn exercise_direct_snapshot(payload: &[u8]) {
    let mut raw = [0_u8; REGION_BYTES];
    fill_repeating(&mut raw, payload);
    exercise_snapshot(&CommitRegion::from_raw_bytes(raw));
}

fn exercise_boundary_policies(source_bytes: &[u8; COMPLETE_SOURCE_BYTES]) {
    let complete_source = COMPLETE_SOURCE_BYTES as u64;
    let region = COMMIT_REGION_LENGTH.get();
    let scenarios = [
        ReadLimits::new(
            ByteCount::new(complete_source),
            ByteCount::new(region),
            ByteCount::new(region),
        ),
        ReadLimits::new(
            ByteCount::new(complete_source - 1),
            ByteCount::new(region),
            ByteCount::new(region),
        ),
        ReadLimits::new(
            ByteCount::new(complete_source),
            ByteCount::new(ONE_BELOW_REGION),
            ByteCount::new(region),
        ),
        ReadLimits::new(
            ByteCount::new(complete_source),
            ByteCount::new(region),
            ByteCount::new(ONE_BELOW_REGION),
        ),
    ];

    for limits in scenarios {
        exercise_reader(source_bytes, limits);
    }

    for truncated in [
        0,
        COMMIT_REGION_OFFSET.get() as usize,
        COMPLETE_SOURCE_BYTES.saturating_sub(1),
    ] {
        if let Some(source) = source_bytes.get(..truncated) {
            exercise_reader(
                source,
                ReadLimits::new(
                    ByteCount::new(truncated as u64),
                    ByteCount::new(region),
                    ByteCount::new(region),
                ),
            );
        }
    }
}

fn exercise_reader(source_bytes: &[u8], limits: ReadLimits) {
    let read_result = with_source(source_bytes, limits, |source, budget| {
        read_commit_region(source, budget)
    });
    match read_result {
        Ok(snapshot) => {
            exercise_snapshot(&snapshot);
            if let Some(expected) = source_bytes.get(
                COMMIT_REGION_OFFSET.get() as usize
                    ..(COMMIT_REGION_OFFSET.get() + COMMIT_REGION_LENGTH.get()) as usize,
            ) {
                assert_eq!(snapshot.raw_bytes().as_slice(), expected);
            }
        }
        Err(error) => {
            black_box(error);
        }
    }

    let sentinel = CommitRegion::from_raw_bytes([0xa5_u8; REGION_BYTES]);
    let mut destination = sentinel.clone();
    let into_result = with_source(source_bytes, limits, |source, budget| {
        read_commit_region_into(source, &mut destination, budget)
    });
    if into_result.is_err() {
        assert_eq!(destination, sentinel);
    } else {
        exercise_snapshot(&destination);
    }
    let _ = black_box(into_result);
}

fn with_source<T>(
    source_bytes: &[u8],
    limits: ReadLimits,
    operation: impl FnOnce(&mut SliceSource<'_>, &mut ReadBudget) -> Result<T, jet3::Error>,
) -> Result<T, jet3::Error> {
    let mut budget = ReadBudget::new(limits);
    let mut source = SliceSource::new(source_bytes, &budget)?;
    let result = operation(&mut source, &mut budget);
    black_box(budget.total_read());
    result
}

fn exercise_snapshot(snapshot: &CommitRegion) {
    assert_eq!(snapshot.raw_slots().len(), COMMIT_SLOT_COUNT);
    for (index, raw) in snapshot.raw_slots().enumerate() {
        let slot = snapshot.slot(index).expect("in-range slot must exist");
        assert_eq!(slot.index() as usize, index);
        assert_eq!(slot.raw(), raw);
        assert_eq!(slot.classification().raw(), raw);
        match slot.role() {
            CommitSlotRole::Exclusive => assert_eq!(index, 0),
            CommitSlotRole::Shared { ordinal } => {
                assert_eq!(usize::from(ordinal) + 1, index);
            }
        }
    }
    assert!(snapshot.slot(COMMIT_SLOT_COUNT).is_none());
    black_box(snapshot.raw_bytes());
}

fn expanded_page(payload: &[u8]) -> [u8; COMPLETE_SOURCE_BYTES] {
    let mut page = [0_u8; COMPLETE_SOURCE_BYTES];
    fill_repeating(&mut page, payload);
    page
}

fn fill_repeating(destination: &mut [u8], source: &[u8]) {
    if source.is_empty() {
        return;
    }
    for (index, byte) in destination.iter_mut().enumerate() {
        if let Some(value) = source.get(index % source.len()) {
            *byte = *value;
        }
    }
}

fn select_input_limit(selector: Option<u8>, source_len: u64) -> u64 {
    match selector.unwrap_or_default() % 5 {
        0 => 0,
        1 => source_len.saturating_sub(1),
        2 => source_len,
        3 => source_len.saturating_add(1),
        _ => COMPLETE_SOURCE_BYTES as u64,
    }
}

fn select_read_limit(selector: Option<u8>, source_len: u64) -> u64 {
    match selector.unwrap_or_default() % 6 {
        0 => 0,
        1 => ONE_BELOW_REGION,
        2 => COMMIT_REGION_LENGTH.get(),
        3 => COMMIT_REGION_LENGTH.get() + 1,
        4 => source_len,
        _ => COMPLETE_SOURCE_BYTES as u64,
    }
}

fn usize_to_u64(value: usize) -> u64 {
    u64::try_from(value).unwrap_or(u64::MAX)
}
