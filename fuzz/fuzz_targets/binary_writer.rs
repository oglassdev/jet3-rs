#![no_main]

use std::hint::black_box;

use jet3::limits::ReadLimits;
use jet3::{BinaryWriter, ByteCount, ByteOffset, ResourceBudget, ResourceLimits};
use libfuzzer_sys::fuzz_target;

const CONTROL_BYTES: usize = 3;
const COMMAND_BYTES: usize = 17;
const MAX_COMMANDS: usize = 128;
const MAX_CAPACITY: usize = 256;
const SENTINEL: u8 = 0xa5;

fuzz_target!(|data: &[u8]| {
    let capacity = select_capacity(data.first().copied(), data.len());
    let encoded_limit = select_limit(data.get(1).copied(), capacity);
    let work_limit = select_limit(data.get(2).copied(), capacity);
    let limits = ResourceLimits::new(ReadLimits::default())
        .with_max_encoded_bytes(ByteCount::new(encoded_limit))
        .with_max_total_work_units(work_limit);

    let mut output = [SENTINEL; MAX_CAPACITY];
    let mut budget = ResourceBudget::new(limits);
    let mut model = Model::new(capacity, encoded_limit, work_limit);
    let commands = data.get(CONTROL_BYTES..).unwrap_or_default();

    for command in commands.chunks(COMMAND_BYTES).take(MAX_COMMANDS) {
        let operation = decode_operation(command, data, capacity);
        let before = model.clone();
        let expected_success = model.apply(&operation);
        if !expected_success {
            assert_eq!(model, before);
        }

        let (result, position, remaining, total_encoded) = {
            let mut writer = BinaryWriter::new(&mut output[..capacity], &mut budget)
                .expect("a fixed 256-byte-or-smaller slice must be representable");
            writer
                .seek(ByteOffset::new(before.position as u64))
                .expect("the model position must remain within capacity");
            assert_eq!(writer.capacity(), ByteCount::new(capacity as u64));

            let result = apply_actual(&mut writer, &operation);
            let position = writer.position();
            let remaining = writer.remaining();
            let total_encoded = writer.total_encoded();
            (result, position, remaining, total_encoded)
        };

        assert_eq!(result.is_ok(), expected_success);
        assert_eq!(output, model.output);
        assert_eq!(position, ByteOffset::new(model.position as u64));
        assert_eq!(
            remaining,
            Ok(ByteCount::new((capacity - model.position) as u64))
        );
        assert_eq!(total_encoded, ByteCount::new(model.encoded));
        assert_eq!(budget.encoded_bytes(), ByteCount::new(model.encoded));
        assert_eq!(budget.total_work_units(), model.work);
        black_box(result.err());
    }
});

#[derive(Clone, Debug, PartialEq, Eq)]
struct Model {
    output: [u8; MAX_CAPACITY],
    capacity: usize,
    position: usize,
    encoded: u64,
    work: u64,
    encoded_limit: u64,
    work_limit: u64,
}

impl Model {
    const fn new(capacity: usize, encoded_limit: u64, work_limit: u64) -> Self {
        Self {
            output: [SENTINEL; MAX_CAPACITY],
            capacity,
            position: 0,
            encoded: 0,
            work: 0,
            encoded_limit,
            work_limit,
        }
    }

    fn apply(&mut self, operation: &Operation<'_>) -> bool {
        match operation {
            Operation::Exact(bytes) => self.write(bytes),
            Operation::U8(value) => self.write(&value.to_le_bytes()),
            Operation::I8(value) => self.write(&value.to_le_bytes()),
            Operation::U16(value) => self.write(&value.to_le_bytes()),
            Operation::I16(value) => self.write(&value.to_le_bytes()),
            Operation::U32(value) => self.write(&value.to_le_bytes()),
            Operation::I32(value) => self.write(&value.to_le_bytes()),
            Operation::U64(value) => self.write(&value.to_le_bytes()),
            Operation::I64(value) => self.write(&value.to_le_bytes()),
            Operation::F32(bits) => self.write(&bits.to_le_bytes()),
            Operation::F64(bits) => self.write(&bits.to_le_bytes()),
            Operation::Seek(position) => {
                let Ok(position) = usize::try_from(*position) else {
                    return false;
                };
                if position > self.capacity {
                    return false;
                }
                self.position = position;
                true
            }
            Operation::Observe => true,
        }
    }

    fn write(&mut self, bytes: &[u8]) -> bool {
        let Some(end) = self.position.checked_add(bytes.len()) else {
            return false;
        };
        let count = bytes.len() as u64;
        let Some(next_encoded) = self.encoded.checked_add(count) else {
            return false;
        };
        let Some(next_work) = self.work.checked_add(count) else {
            return false;
        };
        if end > self.capacity || next_encoded > self.encoded_limit || next_work > self.work_limit {
            return false;
        }

        self.output[self.position..end].copy_from_slice(bytes);
        self.position = end;
        self.encoded = next_encoded;
        self.work = next_work;
        true
    }
}

enum Operation<'data> {
    Exact(&'data [u8]),
    U8(u8),
    I8(i8),
    U16(u16),
    I16(i16),
    U32(u32),
    I32(i32),
    U64(u64),
    I64(i64),
    F32(u32),
    F64(u64),
    Seek(u64),
    Observe,
}

fn decode_operation<'data>(command: &[u8], data: &'data [u8], capacity: usize) -> Operation<'data> {
    let opcode = command.first().copied().unwrap_or_default();
    let first_bytes = command.get(1..9).unwrap_or_default();
    let second_bytes = command.get(9..17).unwrap_or_default();
    let first = decode_u64(first_bytes);
    let second = decode_u64(second_bytes);

    match opcode % 13 {
        0 => Operation::Exact(select_payload(data, first, second)),
        1 => Operation::U8(first as u8),
        2 => Operation::I8(first as i8),
        3 => Operation::U16(first as u16),
        4 => Operation::I16(first as i16),
        5 => Operation::U32(first as u32),
        6 => Operation::I32(first as i32),
        7 => Operation::U64(first),
        8 => Operation::I64(first as i64),
        9 => Operation::F32(first as u32),
        10 => Operation::F64(first),
        11 => Operation::Seek(select_position(
            first_bytes.first().copied(),
            first,
            capacity,
        )),
        _ => Operation::Observe,
    }
}

fn apply_actual(
    writer: &mut BinaryWriter<'_, '_>,
    operation: &Operation<'_>,
) -> Result<(), jet3::Error> {
    match operation {
        Operation::Exact(bytes) => writer.write_exact(bytes),
        Operation::U8(value) => writer.write_u8(*value),
        Operation::I8(value) => writer.write_i8(*value),
        Operation::U16(value) => writer.write_u16_le(*value),
        Operation::I16(value) => writer.write_i16_le(*value),
        Operation::U32(value) => writer.write_u32_le(*value),
        Operation::I32(value) => writer.write_i32_le(*value),
        Operation::U64(value) => writer.write_u64_le(*value),
        Operation::I64(value) => writer.write_i64_le(*value),
        Operation::F32(bits) => writer.write_f32_le(f32::from_bits(*bits)),
        Operation::F64(bits) => writer.write_f64_le(f64::from_bits(*bits)),
        Operation::Seek(position) => writer.seek(ByteOffset::new(*position)),
        Operation::Observe => {
            black_box(writer.position());
            black_box(writer.capacity());
            black_box(writer.remaining()?);
            black_box(writer.total_encoded());
            Ok(())
        }
    }
}

fn select_capacity(selector: Option<u8>, input_len: usize) -> usize {
    match selector.unwrap_or_default() % 7 {
        0 => 0,
        1 => 1,
        2 => 8,
        3 => 64,
        4 => 128,
        5 => MAX_CAPACITY,
        _ => input_len.min(MAX_CAPACITY),
    }
}

fn select_limit(selector: Option<u8>, capacity: usize) -> u64 {
    let capacity = capacity as u64;
    match selector.unwrap_or_default() % 7 {
        0 => 0,
        1 => capacity.saturating_sub(1),
        2 => capacity,
        3 => capacity.saturating_add(1),
        4 => capacity.saturating_mul(2),
        5 => capacity.saturating_mul(8),
        _ => u64::MAX,
    }
}

fn select_position(selector: Option<u8>, raw: u64, capacity: usize) -> u64 {
    let capacity = capacity as u64;
    match selector.unwrap_or_default() % 6 {
        0 => 0,
        1 => capacity.saturating_sub(1),
        2 => capacity,
        3 => capacity.saturating_add(1),
        4 => u64::MAX,
        _ => raw,
    }
}

fn select_payload(data: &[u8], raw_start: u64, raw_length: u64) -> &[u8] {
    if data.is_empty() {
        return data;
    }
    let data_len = data.len() as u64;
    let start = usize::try_from(raw_start % data_len).unwrap_or_default();
    let requested = usize::try_from(raw_length % (MAX_CAPACITY as u64 + 1)).unwrap_or_default();
    let end = start.saturating_add(requested).min(data.len());
    data.get(start..end).unwrap_or_default()
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
