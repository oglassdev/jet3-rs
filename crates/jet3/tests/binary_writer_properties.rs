//! Deterministic properties for the format-neutral checked binary writer.
//!
//! These tests exercise only borrowed-slice encoding and resource accounting.
//! They do not construct an MDB or make a Jet compatibility claim.

use jet3::limits::{ReadBudget, ReadLimits};
use jet3::{BinaryCursor, BinaryWriter, ByteCount, ByteOffset, ResourceBudget, ResourceLimits};
use proptest::prelude::*;
use proptest::test_runner::{Config, RngSeed};

const CASES: u32 = 256;
const SEED: u64 = 0x4a45_5433_5752_4954;

fn deterministic_config() -> Config {
    Config {
        cases: CASES,
        max_shrink_iters: 4_096,
        rng_seed: RngSeed::Fixed(SEED),
        ..Config::default()
    }
}

fn writer_budget(encoded: u64, work: u64) -> ResourceBudget {
    ResourceBudget::new(
        ResourceLimits::new(ReadLimits::default())
            .with_max_encoded_bytes(ByteCount::new(encoded))
            .with_max_total_work_units(work),
    )
}

proptest! {
    #![proptest_config(deterministic_config())]

    #[test]
    fn primitive_bits_roundtrip_through_independent_binary_cursor(
        values in any::<(u8, i8, u16, i16, u32, i32, u64, i64, u32, u64)>(),
    ) {
        let (u8_value, i8_value, u16_value, i16_value, u32_value, i32_value,
            u64_value, i64_value, f32_bits, f64_bits) = values;
        let mut output = [0_u8; 42];
        let mut encode_budget = writer_budget(42, 42);
        {
            let mut writer = BinaryWriter::new(&mut output, &mut encode_budget)?;
            writer.write_u8(u8_value)?;
            writer.write_i8(i8_value)?;
            writer.write_u16_le(u16_value)?;
            writer.write_i16_le(i16_value)?;
            writer.write_u32_le(u32_value)?;
            writer.write_i32_le(i32_value)?;
            writer.write_u64_le(u64_value)?;
            writer.write_i64_le(i64_value)?;
            writer.write_f32_le(f32::from_bits(f32_bits))?;
            writer.write_f64_le(f64::from_bits(f64_bits))?;
            prop_assert_eq!(writer.position(), ByteOffset::new(42));
        }
        prop_assert_eq!(encode_budget.encoded_bytes(), ByteCount::new(42));
        prop_assert_eq!(encode_budget.total_work_units(), 42);

        let mut read_budget = ReadBudget::new(ReadLimits::new(
            ByteCount::new(42),
            ByteCount::new(8),
            ByteCount::new(42),
        ));
        let mut cursor = BinaryCursor::new(&output, &mut read_budget)?;
        prop_assert_eq!(cursor.read_u8()?, u8_value);
        prop_assert_eq!(cursor.read_i8()?, i8_value);
        prop_assert_eq!(cursor.read_u16_le()?, u16_value);
        prop_assert_eq!(cursor.read_i16_le()?, i16_value);
        prop_assert_eq!(cursor.read_u32_le()?, u32_value);
        prop_assert_eq!(cursor.read_i32_le()?, i32_value);
        prop_assert_eq!(cursor.read_u64_le()?, u64_value);
        prop_assert_eq!(cursor.read_i64_le()?, i64_value);
        prop_assert_eq!(cursor.read_f32_le()?.to_bits(), f32_bits);
        prop_assert_eq!(cursor.read_f64_le()?.to_bits(), f64_bits);
        prop_assert_eq!(cursor.remaining()?, ByteCount::new(0));
    }

    #[test]
    fn exact_write_matches_independent_capacity_and_budget_model(
        output_len in 0_usize..=128,
        raw_position in 0_usize..=128,
        bytes in proptest::collection::vec(any::<u8>(), 0..=128),
        encoded_limit in 0_u64..=256,
        work_limit in 0_u64..=256,
    ) {
        let position = raw_position.min(output_len);
        let count = bytes.len() as u64;
        let fits = bytes.len() <= output_len - position;
        let should_succeed =
            fits && count <= encoded_limit && count <= work_limit;
        let mut output = vec![0xa5; output_len];
        let before = output.clone();
        let mut budget = writer_budget(encoded_limit, work_limit);
        let (result, final_position, total_encoded) = {
            let mut writer = BinaryWriter::new(&mut output, &mut budget)?;
            writer.seek(ByteOffset::new(position as u64))?;
            let result = writer.write_exact(&bytes);
            (result, writer.position(), writer.total_encoded())
        };

        prop_assert_eq!(result.is_ok(), should_succeed);
        if should_succeed {
            let end = position + bytes.len();
            let mut expected = before;
            expected[position..end].copy_from_slice(&bytes);
            prop_assert_eq!(output, expected);
            prop_assert_eq!(final_position, ByteOffset::new(end as u64));
            prop_assert_eq!(total_encoded, ByteCount::new(count));
            prop_assert_eq!(budget.total_work_units(), count);
        } else {
            prop_assert_eq!(output, before);
            prop_assert_eq!(final_position, ByteOffset::new(position as u64));
            prop_assert_eq!(total_encoded, ByteCount::new(0));
            prop_assert_eq!(budget.total_work_units(), 0);
        }
    }
}
