//! Checked, budgeted primitive encoding into a borrowed byte slice.

use crate::{ByteCount, ByteOffset, Error, ResourceBudget};

/// A cursor that writes little-endian primitives into a fixed borrowed slice.
///
/// The writer never allocates or grows its output. Every operation validates
/// its complete destination range and operation-wide encoded-byte budget
/// before changing any byte, position, or counter. Seeking does not reset
/// cumulative accounting, so rewriting an earlier range consumes budget again.
#[derive(Debug)]
pub struct BinaryWriter<'output, 'budget> {
    output: &'output mut [u8],
    capacity: ByteCount,
    position: ByteOffset,
    budget: &'budget mut ResourceBudget,
}

impl<'output, 'budget> BinaryWriter<'output, 'budget> {
    /// Creates a writer over the caller-owned fixed output slice.
    pub fn new(
        output: &'output mut [u8],
        budget: &'budget mut ResourceBudget,
    ) -> Result<Self, Error> {
        let capacity = ByteCount::from_usize(output.len())?;
        Ok(Self {
            output,
            capacity,
            position: ByteOffset::new(0),
            budget,
        })
    }

    /// Returns the current absolute byte position.
    #[must_use]
    pub const fn position(&self) -> ByteOffset {
        self.position
    }

    /// Returns the fixed output capacity.
    #[must_use]
    pub const fn capacity(&self) -> ByteCount {
        self.capacity
    }

    /// Returns the bytes between the current position and the output end.
    pub fn remaining(&self) -> Result<ByteCount, Error> {
        self.capacity
            .checked_sub(ByteCount::new(self.position.get()))
    }

    /// Returns cumulative bytes successfully encoded, including rewrites.
    #[must_use]
    pub const fn total_encoded(&self) -> ByteCount {
        self.budget.encoded_bytes()
    }

    /// Moves to an absolute output position without resetting the budget.
    ///
    /// The exact end position is valid; positions beyond it are rejected.
    pub fn seek(&mut self, position: ByteOffset) -> Result<(), Error> {
        if position.get() > self.capacity.get() {
            return Err(Error::OutputOffsetOutOfBounds {
                offset: position,
                capacity: self.capacity,
            });
        }
        self.position = position;
        Ok(())
    }

    /// Writes the complete byte slice after capacity and budget preflight.
    pub fn write_exact(&mut self, bytes: &[u8]) -> Result<(), Error> {
        let count = ByteCount::from_usize(bytes.len())?;
        let end = self.position.checked_add(count)?;
        if end.get() > self.capacity.get() {
            return Err(Error::OutputCapacityExceeded {
                offset: self.position,
                needed: count,
                available: self.remaining()?,
            });
        }

        let start_index = self.position.to_usize()?;
        let end_index = end.to_usize()?;
        let available = self.remaining()?;
        let destination =
            self.output
                .get_mut(start_index..end_index)
                .ok_or(Error::OutputCapacityExceeded {
                    offset: self.position,
                    needed: count,
                    available,
                })?;

        self.budget.charge_encoded_bytes(count)?;
        destination.copy_from_slice(bytes);
        self.position = end;
        Ok(())
    }

    /// Writes one unsigned byte.
    pub fn write_u8(&mut self, value: u8) -> Result<(), Error> {
        self.write_exact(&value.to_le_bytes())
    }

    /// Writes one signed byte.
    pub fn write_i8(&mut self, value: i8) -> Result<(), Error> {
        self.write_exact(&value.to_le_bytes())
    }

    /// Writes a little-endian unsigned 16-bit integer.
    pub fn write_u16_le(&mut self, value: u16) -> Result<(), Error> {
        self.write_exact(&value.to_le_bytes())
    }

    /// Writes a little-endian signed 16-bit integer.
    pub fn write_i16_le(&mut self, value: i16) -> Result<(), Error> {
        self.write_exact(&value.to_le_bytes())
    }

    /// Writes a little-endian unsigned 32-bit integer.
    pub fn write_u32_le(&mut self, value: u32) -> Result<(), Error> {
        self.write_exact(&value.to_le_bytes())
    }

    /// Writes a little-endian signed 32-bit integer.
    pub fn write_i32_le(&mut self, value: i32) -> Result<(), Error> {
        self.write_exact(&value.to_le_bytes())
    }

    /// Writes a little-endian unsigned 64-bit integer.
    pub fn write_u64_le(&mut self, value: u64) -> Result<(), Error> {
        self.write_exact(&value.to_le_bytes())
    }

    /// Writes a little-endian signed 64-bit integer.
    pub fn write_i64_le(&mut self, value: i64) -> Result<(), Error> {
        self.write_exact(&value.to_le_bytes())
    }

    /// Writes an IEEE-754 little-endian 32-bit float without normalizing bits.
    pub fn write_f32_le(&mut self, value: f32) -> Result<(), Error> {
        self.write_exact(&value.to_le_bytes())
    }

    /// Writes an IEEE-754 little-endian 64-bit float without normalizing bits.
    pub fn write_f64_le(&mut self, value: f64) -> Result<(), Error> {
        self.write_exact(&value.to_le_bytes())
    }
}

#[cfg(test)]
#[path = "binary_writer_tests.rs"]
mod tests;
