use super::BinaryWriter;
use crate::limits::ReadLimits;
use crate::{ByteCount, ByteOffset, Error, ResourceBudget, ResourceLimitKind, ResourceLimits};

fn limits(encoded: u64, work: u64) -> ResourceLimits {
    ResourceLimits::new(ReadLimits::default())
        .with_max_encoded_bytes(ByteCount::new(encoded))
        .with_max_total_work_units(work)
}

#[test]
fn fixed_capacity_seek_and_empty_write_obey_exact_boundaries() -> Result<(), Error> {
    let mut output = [0xa5; 2];
    let mut budget = ResourceBudget::new(limits(0, 0));
    {
        let mut writer = BinaryWriter::new(&mut output, &mut budget)?;
        assert_eq!(writer.capacity(), ByteCount::new(2));
        assert_eq!(writer.remaining(), Ok(ByteCount::new(2)));
        assert_eq!(writer.seek(ByteOffset::new(2)), Ok(()));
        assert_eq!(writer.write_exact(&[]), Ok(()));
        assert_eq!(writer.position(), ByteOffset::new(2));
        assert_eq!(writer.total_encoded(), ByteCount::new(0));
        assert_eq!(
            writer.seek(ByteOffset::new(3)),
            Err(Error::OutputOffsetOutOfBounds {
                offset: ByteOffset::new(3),
                capacity: ByteCount::new(2),
            })
        );
        assert_eq!(writer.position(), ByteOffset::new(2));
    }
    assert_eq!(output, [0xa5; 2]);
    assert_eq!(budget.total_work_units(), 0);
    Ok(())
}

#[test]
fn exact_writes_and_rewrites_advance_cumulative_accounting() -> Result<(), Error> {
    let mut output = [0_u8; 4];
    let mut budget = ResourceBudget::new(limits(6, 6));
    {
        let mut writer = BinaryWriter::new(&mut output, &mut budget)?;
        writer.write_exact(&[1, 2, 3, 4])?;
        writer.seek(ByteOffset::new(1))?;
        writer.write_exact(&[8, 9])?;
        assert_eq!(writer.position(), ByteOffset::new(3));
        assert_eq!(writer.total_encoded(), ByteCount::new(6));
    }
    assert_eq!(output, [1, 8, 9, 4]);
    assert_eq!(budget.encoded_bytes(), ByteCount::new(6));
    assert_eq!(budget.total_work_units(), 6);
    Ok(())
}

#[test]
fn capacity_failure_preserves_bytes_position_and_budget() -> Result<(), Error> {
    let mut output = [0xa5; 3];
    let mut budget = ResourceBudget::new(limits(8, 8));
    {
        let mut writer = BinaryWriter::new(&mut output, &mut budget)?;
        writer.seek(ByteOffset::new(2))?;
        assert_eq!(
            writer.write_exact(&[1, 2]),
            Err(Error::OutputCapacityExceeded {
                offset: ByteOffset::new(2),
                needed: ByteCount::new(2),
                available: ByteCount::new(1),
            })
        );
        assert_eq!(writer.position(), ByteOffset::new(2));
        assert_eq!(writer.total_encoded(), ByteCount::new(0));
    }
    assert_eq!(output, [0xa5; 3]);
    assert_eq!(budget.total_work_units(), 0);
    Ok(())
}

#[test]
fn encoded_and_aggregate_limit_failures_are_atomic() -> Result<(), Error> {
    let mut encoded_output = [0xa5; 2];
    let mut encoded_budget = ResourceBudget::new(limits(1, 2));
    {
        let mut encoded_writer = BinaryWriter::new(&mut encoded_output, &mut encoded_budget)?;
        assert_eq!(
            encoded_writer.write_exact(&[1, 2]),
            Err(Error::ResourceLimitExceeded {
                kind: ResourceLimitKind::EncodedBytes,
                requested: 2,
                maximum: 1,
            })
        );
        assert_eq!(encoded_writer.position(), ByteOffset::new(0));
    }
    assert_eq!(encoded_output, [0xa5; 2]);
    assert_eq!(encoded_budget.encoded_bytes(), ByteCount::new(0));
    assert_eq!(encoded_budget.total_work_units(), 0);

    let mut work_output = [0xa5; 2];
    let mut work_budget = ResourceBudget::new(limits(2, 1));
    {
        let mut work_writer = BinaryWriter::new(&mut work_output, &mut work_budget)?;
        assert_eq!(
            work_writer.write_exact(&[1, 2]),
            Err(Error::ResourceLimitExceeded {
                kind: ResourceLimitKind::TotalWorkUnits,
                requested: 2,
                maximum: 1,
            })
        );
        assert_eq!(work_writer.position(), ByteOffset::new(0));
    }
    assert_eq!(work_output, [0xa5; 2]);
    assert_eq!(work_budget.encoded_bytes(), ByteCount::new(0));
    assert_eq!(work_budget.total_work_units(), 0);
    Ok(())
}

#[test]
fn primitive_encoders_use_little_endian_and_preserve_float_bits() -> Result<(), Error> {
    let mut output = [0_u8; 42];
    let mut budget = ResourceBudget::new(limits(42, 42));
    {
        let mut writer = BinaryWriter::new(&mut output, &mut budget)?;
        writer.write_u8(0xfe)?;
        writer.write_i8(-128)?;
        writer.write_u16_le(0x1234)?;
        writer.write_i16_le(-2)?;
        writer.write_u32_le(0x1234_5678)?;
        writer.write_i32_le(-2)?;
        writer.write_u64_le(0x0123_4567_89ab_cdef)?;
        writer.write_i64_le(-2)?;
        writer.write_f32_le(f32::from_bits(0x7fc0_1234))?;
        writer.write_f64_le(f64::from_bits(0x7ff8_0000_0000_1234))?;
        assert_eq!(writer.position(), ByteOffset::new(42));
    }

    let expected = [
        0xfe, 0x80, 0x34, 0x12, 0xfe, 0xff, 0x78, 0x56, 0x34, 0x12, 0xfe, 0xff, 0xff, 0xff, 0xef,
        0xcd, 0xab, 0x89, 0x67, 0x45, 0x23, 0x01, 0xfe, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
        0x34, 0x12, 0xc0, 0x7f, 0x34, 0x12, 0x00, 0x00, 0x00, 0x00, 0xf8, 0x7f,
    ];
    assert_eq!(output, expected);
    assert_eq!(budget.encoded_bytes(), ByteCount::new(42));
    assert_eq!(budget.total_work_units(), 42);
    Ok(())
}
