#![no_main]

use std::hint::black_box;

use jet3::{BinaryCursor, ByteCount, ByteOffset, ReadBudget, ReadLimits};
use libfuzzer_sys::fuzz_target;

const CONTROL_BYTES: usize = 4;
const COMMAND_BYTES: usize = 9;
const MAX_COMMANDS: usize = 256;

fuzz_target!(|data: &[u8]| {
    let input_len = u64::try_from(data.len()).unwrap_or(u64::MAX);
    let max_input = select_limit(data.first().copied(), input_len);
    let max_single = select_limit(data.get(1).copied(), input_len);
    let max_total = select_limit(data.get(2).copied(), input_len);
    let limits = ReadLimits::new(
        ByteCount::new(max_input),
        ByteCount::new(max_single),
        ByteCount::new(max_total),
    );

    exercise_checked_values(data, limits);

    let mut cursor_budget = ReadBudget::new(limits);
    let Ok(mut cursor) = BinaryCursor::new(data, &mut cursor_budget) else {
        return;
    };

    let commands = match data.get(CONTROL_BYTES..) {
        Some(commands) => commands,
        None => &[],
    };
    for command in commands.chunks(COMMAND_BYTES).take(MAX_COMMANDS) {
        let Some((&opcode, operand)) = command.split_first() else {
            continue;
        };
        let value = decode_u64(operand);

        match opcode % 14 {
            0 => {
                let _ = black_box(cursor.seek(ByteOffset::new(value)));
            }
            1 => {
                let _ = black_box(cursor.read_exact(ByteCount::new(value)));
            }
            2 => {
                let _ = black_box(cursor.skip(ByteCount::new(value)));
            }
            3 => {
                let _ = black_box(cursor.read_u8());
            }
            4 => {
                let _ = black_box(cursor.read_i8());
            }
            5 => {
                let _ = black_box(cursor.read_u16_le());
            }
            6 => {
                let _ = black_box(cursor.read_i16_le());
            }
            7 => {
                let _ = black_box(cursor.read_u32_le());
            }
            8 => {
                let _ = black_box(cursor.read_i32_le());
            }
            9 => {
                let _ = black_box(cursor.read_u64_le());
            }
            10 => {
                let _ = black_box(cursor.read_i64_le());
            }
            11 => {
                let _ = black_box(cursor.read_f32_le());
            }
            12 => {
                let _ = black_box(cursor.read_f64_le());
            }
            _ => {
                let _ = black_box(cursor.remaining());
                black_box(cursor.position());
                black_box(cursor.total_read());
            }
        }
    }
});

fn select_limit(selector: Option<u8>, input_len: u64) -> u64 {
    let selector = selector.unwrap_or_default();
    match selector % 6 {
        0 => 0,
        1 => input_len.saturating_sub(1),
        2 => input_len,
        3 => input_len.saturating_add(1),
        4 => u64::MAX,
        _ => input_len.saturating_mul(2),
    }
}

fn exercise_checked_values(data: &[u8], limits: ReadLimits) {
    let mut budget = ReadBudget::new(limits);
    for chunk in data.chunks(8).take(MAX_COMMANDS) {
        let value = decode_u64(chunk);
        let rotated = value.rotate_left(17);
        let offset = ByteOffset::new(value);
        let count = ByteCount::new(rotated);

        let _ = black_box(offset.checked_add(count));
        let _ = black_box(offset.checked_sub(count));
        let _ = black_box(offset.to_usize());
        let _ = black_box(count.checked_add(ByteCount::new(value)));
        let _ = black_box(count.checked_sub(ByteCount::new(value)));
        let _ = black_box(count.to_usize());

        let _ = black_box(budget.check_input(count));
        let _ = black_box(budget.check_read(ByteCount::new(value)));
        let _ = black_box(budget.charge_read_attempt(ByteCount::new(value)));
        let _ = black_box(budget.check_read(count));
        let _ = black_box(budget.charge_read_attempt(count));
        black_box(budget.total_read());
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
