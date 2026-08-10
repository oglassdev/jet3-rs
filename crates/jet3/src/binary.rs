//! Checked, budgeted reads over an immutable byte slice.

use crate::limits::ReadBudget;
use crate::{ByteCount, ByteOffset, Error};

/// A cursor that reads little-endian primitives from a borrowed slice.
///
/// Every read checks its range before slicing and charges the requested bytes
/// against both the single-read and cumulative limits in [`ReadBudget`].
/// Seeking does not reset the cumulative budget.
#[derive(Debug)]
pub struct BinaryCursor<'input, 'budget> {
    input: &'input [u8],
    position: ByteOffset,
    budget: &'budget mut ReadBudget,
}

impl<'input, 'budget> BinaryCursor<'input, 'budget> {
    /// Creates a cursor after enforcing the configured input-length ceiling.
    pub fn new(input: &'input [u8], budget: &'budget mut ReadBudget) -> Result<Self, Error> {
        let input_len = ByteCount::from_usize(input.len())?;
        budget.check_input(input_len)?;
        Ok(Self {
            input,
            position: ByteOffset::new(0),
            budget,
        })
    }

    /// Returns the current absolute byte position.
    #[must_use]
    pub const fn position(&self) -> ByteOffset {
        self.position
    }

    /// Returns the cumulative number of bytes read or skipped.
    #[must_use]
    pub const fn total_read(&self) -> ByteCount {
        self.budget.total_read()
    }

    /// Returns the number of bytes between the cursor and the input end.
    pub fn remaining(&self) -> Result<ByteCount, Error> {
        let input_len = ByteCount::from_usize(self.input.len())?;
        input_len.checked_sub(ByteCount::new(self.position.get()))
    }

    /// Moves to an absolute position without resetting the work budget.
    ///
    /// The end position is valid; positions beyond the end are rejected.
    pub fn seek(&mut self, position: ByteOffset) -> Result<(), Error> {
        let input_len = ByteCount::from_usize(self.input.len())?;
        if position.get() > input_len.get() {
            return Err(Error::OffsetOutOfBounds {
                offset: position,
                input_len,
            });
        }
        self.position = position;
        Ok(())
    }

    /// Advances by `count` bytes and charges them to the read budget.
    pub fn skip(&mut self, count: ByteCount) -> Result<(), Error> {
        self.read_exact(count).map(|_| ())
    }

    /// Returns exactly `count` bytes after all range and budget checks.
    pub fn read_exact(&mut self, count: ByteCount) -> Result<&'input [u8], Error> {
        self.budget.check_read(count)?;
        let end = self.position.checked_add(count)?;
        let input_len = ByteCount::from_usize(self.input.len())?;
        if end.get() > input_len.get() {
            return Err(Error::UnexpectedEnd {
                offset: self.position,
                needed: count,
                available: self.remaining()?,
            });
        }

        self.budget.charge_read_attempt(count)?;
        let start_index = self.position.to_usize()?;
        let end_index = end.to_usize()?;
        let bytes = self
            .input
            .get(start_index..end_index)
            .ok_or(Error::UnexpectedEnd {
                offset: self.position,
                needed: count,
                available: self.remaining()?,
            })?;
        self.position = end;
        Ok(bytes)
    }

    /// Reads one unsigned byte.
    pub fn read_u8(&mut self) -> Result<u8, Error> {
        let bytes = self.read_array::<1>()?;
        Ok(bytes[0])
    }

    /// Reads one signed byte.
    pub fn read_i8(&mut self) -> Result<i8, Error> {
        Ok(i8::from_le_bytes(self.read_array::<1>()?))
    }

    /// Reads a little-endian unsigned 16-bit integer.
    pub fn read_u16_le(&mut self) -> Result<u16, Error> {
        Ok(u16::from_le_bytes(self.read_array::<2>()?))
    }

    /// Reads a little-endian signed 16-bit integer.
    pub fn read_i16_le(&mut self) -> Result<i16, Error> {
        Ok(i16::from_le_bytes(self.read_array::<2>()?))
    }

    /// Reads a little-endian unsigned 32-bit integer.
    pub fn read_u32_le(&mut self) -> Result<u32, Error> {
        Ok(u32::from_le_bytes(self.read_array::<4>()?))
    }

    /// Reads a little-endian signed 32-bit integer.
    pub fn read_i32_le(&mut self) -> Result<i32, Error> {
        Ok(i32::from_le_bytes(self.read_array::<4>()?))
    }

    /// Reads a little-endian unsigned 64-bit integer.
    pub fn read_u64_le(&mut self) -> Result<u64, Error> {
        Ok(u64::from_le_bytes(self.read_array::<8>()?))
    }

    /// Reads a little-endian signed 64-bit integer.
    pub fn read_i64_le(&mut self) -> Result<i64, Error> {
        Ok(i64::from_le_bytes(self.read_array::<8>()?))
    }

    /// Reads an IEEE-754 little-endian 32-bit float.
    pub fn read_f32_le(&mut self) -> Result<f32, Error> {
        Ok(f32::from_le_bytes(self.read_array::<4>()?))
    }

    /// Reads an IEEE-754 little-endian 64-bit float.
    pub fn read_f64_le(&mut self) -> Result<f64, Error> {
        Ok(f64::from_le_bytes(self.read_array::<8>()?))
    }

    fn read_array<const N: usize>(&mut self) -> Result<[u8; N], Error> {
        let count = ByteCount::from_usize(N)?;
        let bytes = self.read_exact(count)?;
        <[u8; N]>::try_from(bytes).map_err(|_| Error::UnexpectedEnd {
            offset: self.position,
            needed: count,
            available: ByteCount::new(0),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::BinaryCursor;
    use crate::limits::{ReadBudget, ReadLimits};
    use crate::{ByteCount, ByteOffset, Error, LimitKind};

    fn budget(input: u64, single: u64, total: u64) -> ReadBudget {
        ReadBudget::new(ReadLimits::new(
            ByteCount::new(input),
            ByteCount::new(single),
            ByteCount::new(total),
        ))
    }

    #[test]
    fn constructor_accepts_input_at_limit_and_rejects_one_above() {
        let mut exact_budget = budget(3, 3, 3);
        assert!(BinaryCursor::new(&[0; 3], &mut exact_budget).is_ok());
        let mut small_budget = budget(3, 4, 4);
        assert_eq!(
            BinaryCursor::new(&[0; 4], &mut small_budget).err(),
            Some(Error::LimitExceeded {
                kind: LimitKind::InputBytes,
                requested: ByteCount::new(4),
                maximum: ByteCount::new(3),
            })
        );
    }

    #[test]
    fn seek_accepts_start_and_end_but_rejects_past_end() -> Result<(), Error> {
        let mut read_budget = budget(2, 2, 2);
        let mut cursor = BinaryCursor::new(&[1, 2], &mut read_budget)?;
        assert_eq!(cursor.seek(ByteOffset::new(2)), Ok(()));
        assert_eq!(cursor.position(), ByteOffset::new(2));
        assert_eq!(cursor.remaining(), Ok(ByteCount::new(0)));
        assert_eq!(
            cursor.seek(ByteOffset::new(3)),
            Err(Error::OffsetOutOfBounds {
                offset: ByteOffset::new(3),
                input_len: ByteCount::new(2),
            })
        );
        assert_eq!(cursor.position(), ByteOffset::new(2));
        assert_eq!(cursor.seek(ByteOffset::new(0)), Ok(()));
        Ok(())
    }

    #[test]
    fn empty_read_at_end_succeeds_without_work() -> Result<(), Error> {
        let mut read_budget = budget(0, 0, 0);
        let mut cursor = BinaryCursor::new(&[], &mut read_budget)?;
        assert_eq!(cursor.read_exact(ByteCount::new(0)), Ok(&[][..]));
        assert_eq!(cursor.position(), ByteOffset::new(0));
        assert_eq!(cursor.total_read(), ByteCount::new(0));
        Ok(())
    }

    #[test]
    fn exact_read_advances_position_and_work() -> Result<(), Error> {
        let input = [1, 2, 3];
        let mut read_budget = budget(3, 3, 3);
        let mut cursor = BinaryCursor::new(&input, &mut read_budget)?;
        assert_eq!(cursor.read_exact(ByteCount::new(2)), Ok(&input[..2]));
        assert_eq!(cursor.position(), ByteOffset::new(2));
        assert_eq!(cursor.total_read(), ByteCount::new(2));
        assert_eq!(cursor.remaining(), Ok(ByteCount::new(1)));
        assert_eq!(cursor.skip(ByteCount::new(1)), Ok(()));
        assert_eq!(cursor.position(), ByteOffset::new(3));
        assert_eq!(cursor.total_read(), ByteCount::new(3));
        Ok(())
    }

    #[test]
    fn one_read_limit_rejects_one_above_without_advancing() -> Result<(), Error> {
        let mut read_budget = budget(3, 2, 3);
        let mut cursor = BinaryCursor::new(&[0; 3], &mut read_budget)?;
        assert_eq!(
            cursor.read_exact(ByteCount::new(3)),
            Err(Error::LimitExceeded {
                kind: LimitKind::SingleReadBytes,
                requested: ByteCount::new(3),
                maximum: ByteCount::new(2),
            })
        );
        assert_eq!(cursor.position(), ByteOffset::new(0));
        assert_eq!(cursor.total_read(), ByteCount::new(0));
        Ok(())
    }

    #[test]
    fn cumulative_limit_counts_rereads_and_preserves_state_on_failure() -> Result<(), Error> {
        let mut read_budget = budget(2, 2, 3);
        let mut cursor = BinaryCursor::new(&[0; 2], &mut read_budget)?;
        assert!(cursor.read_exact(ByteCount::new(2)).is_ok());
        assert_eq!(cursor.seek(ByteOffset::new(0)), Ok(()));
        assert_eq!(
            cursor.read_exact(ByteCount::new(2)),
            Err(Error::LimitExceeded {
                kind: LimitKind::TotalReadBytes,
                requested: ByteCount::new(4),
                maximum: ByteCount::new(3),
            })
        );
        assert_eq!(cursor.position(), ByteOffset::new(0));
        assert_eq!(cursor.total_read(), ByteCount::new(2));
        Ok(())
    }

    #[test]
    fn truncated_read_reports_offset_needed_and_available() -> Result<(), Error> {
        let mut read_budget = budget(3, 3, 3);
        let mut cursor = BinaryCursor::new(&[1, 2, 3], &mut read_budget)?;
        assert_eq!(cursor.seek(ByteOffset::new(2)), Ok(()));
        assert_eq!(
            cursor.read_exact(ByteCount::new(2)),
            Err(Error::UnexpectedEnd {
                offset: ByteOffset::new(2),
                needed: ByteCount::new(2),
                available: ByteCount::new(1),
            })
        );
        assert_eq!(cursor.position(), ByteOffset::new(2));
        assert_eq!(cursor.total_read(), ByteCount::new(0));
        Ok(())
    }

    #[test]
    fn huge_range_reports_truncation_without_allocating() -> Result<(), Error> {
        let mut read_budget = budget(0, u64::MAX, u64::MAX);
        let mut cursor = BinaryCursor::new(&[], &mut read_budget)?;
        assert_eq!(cursor.seek(ByteOffset::new(0)), Ok(()));
        assert_eq!(
            cursor.read_exact(ByteCount::new(u64::MAX)),
            Err(Error::UnexpectedEnd {
                offset: ByteOffset::new(0),
                needed: ByteCount::new(u64::MAX),
                available: ByteCount::new(0),
            })
        );
        Ok(())
    }

    #[test]
    fn little_endian_integer_primitives_decode_exactly() -> Result<(), Error> {
        let input = [
            0xfe, 0x80, 0x34, 0x12, 0xfe, 0xff, 0x78, 0x56, 0x34, 0x12, 0xfe, 0xff, 0xff, 0xff,
            0xef, 0xcd, 0xab, 0x89, 0x67, 0x45, 0x23, 0x01, 0xfe, 0xff, 0xff, 0xff, 0xff, 0xff,
            0xff, 0xff,
        ];
        let mut read_budget = budget(30, 8, 30);
        let mut cursor = BinaryCursor::new(&input, &mut read_budget)?;
        assert_eq!(cursor.read_u8(), Ok(0xfe));
        assert_eq!(cursor.read_i8(), Ok(-128));
        assert_eq!(cursor.read_u16_le(), Ok(0x1234));
        assert_eq!(cursor.read_i16_le(), Ok(-2));
        assert_eq!(cursor.read_u32_le(), Ok(0x1234_5678));
        assert_eq!(cursor.read_i32_le(), Ok(-2));
        assert_eq!(cursor.read_u64_le(), Ok(0x0123_4567_89ab_cdef));
        assert_eq!(cursor.read_i64_le(), Ok(-2));
        Ok(())
    }

    #[test]
    fn little_endian_float_primitives_preserve_bits() -> Result<(), Error> {
        let mut input = [0_u8; 12];
        input[..4].copy_from_slice(&0x7fc0_1234_u32.to_le_bytes());
        input[4..].copy_from_slice(&0x7ff8_0000_0000_1234_u64.to_le_bytes());
        let mut read_budget = budget(12, 8, 12);
        let mut cursor = BinaryCursor::new(&input, &mut read_budget)?;
        assert_eq!(cursor.read_f32_le().map(f32::to_bits), Ok(0x7fc0_1234));
        assert_eq!(
            cursor.read_f64_le().map(f64::to_bits),
            Ok(0x7ff8_0000_0000_1234)
        );
        Ok(())
    }

    #[test]
    fn primitive_read_propagates_truncation_without_advancing() -> Result<(), Error> {
        let mut read_budget = budget(1, 8, 8);
        let mut cursor = BinaryCursor::new(&[1], &mut read_budget)?;
        assert_eq!(
            cursor.read_u16_le(),
            Err(Error::UnexpectedEnd {
                offset: ByteOffset::new(0),
                needed: ByteCount::new(2),
                available: ByteCount::new(1),
            })
        );
        assert_eq!(cursor.position(), ByteOffset::new(0));
        assert_eq!(cursor.total_read(), ByteCount::new(0));
        Ok(())
    }
}
