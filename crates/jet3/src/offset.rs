//! Strong byte positions and lengths with checked arithmetic.

use crate::Error;

/// An absolute byte position.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct ByteOffset(u64);

impl ByteOffset {
    /// Creates an offset without performing arithmetic.
    #[must_use]
    pub const fn new(value: u64) -> Self {
        Self(value)
    }

    /// Returns the underlying byte position.
    #[must_use]
    pub const fn get(self) -> u64 {
        self.0
    }

    /// Adds a byte count, rejecting overflow.
    pub fn checked_add(self, count: ByteCount) -> Result<Self, Error> {
        self.0
            .checked_add(count.0)
            .map(Self)
            .ok_or(Error::Arithmetic {
                operation: "offset addition",
            })
    }

    /// Subtracts a byte count, rejecting underflow.
    pub fn checked_sub(self, count: ByteCount) -> Result<Self, Error> {
        self.0
            .checked_sub(count.0)
            .map(Self)
            .ok_or(Error::Arithmetic {
                operation: "offset subtraction",
            })
    }

    /// Converts this offset to a slice index.
    pub fn to_usize(self) -> Result<usize, Error> {
        usize::try_from(self.0).map_err(|_| Error::IntegerConversion {
            value: u128::from(self.0),
            target: "usize",
        })
    }
}

/// A number of bytes.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct ByteCount(u64);

impl ByteCount {
    /// Creates a byte count without performing arithmetic.
    #[must_use]
    pub const fn new(value: u64) -> Self {
        Self(value)
    }

    /// Returns the underlying number of bytes.
    #[must_use]
    pub const fn get(self) -> u64 {
        self.0
    }

    /// Adds two counts, rejecting overflow.
    pub fn checked_add(self, other: Self) -> Result<Self, Error> {
        self.0
            .checked_add(other.0)
            .map(Self)
            .ok_or(Error::Arithmetic {
                operation: "byte-count addition",
            })
    }

    /// Subtracts a count, rejecting underflow.
    pub fn checked_sub(self, other: Self) -> Result<Self, Error> {
        self.0
            .checked_sub(other.0)
            .map(Self)
            .ok_or(Error::Arithmetic {
                operation: "byte-count subtraction",
            })
    }

    /// Converts this count to a slice length.
    pub fn to_usize(self) -> Result<usize, Error> {
        usize::try_from(self.0).map_err(|_| Error::IntegerConversion {
            value: u128::from(self.0),
            target: "usize",
        })
    }

    /// Converts a slice length to a byte count.
    pub fn from_usize(value: usize) -> Result<Self, Error> {
        u64::try_from(value)
            .map(Self)
            .map_err(|_| Error::IntegerConversion {
                value: value as u128,
                target: "u64",
            })
    }
}

#[cfg(test)]
mod tests {
    use super::{ByteCount, ByteOffset};
    use crate::Error;

    #[test]
    fn offset_arithmetic_accepts_exact_boundaries() {
        assert_eq!(
            ByteOffset::new(u64::MAX - 1).checked_add(ByteCount::new(1)),
            Ok(ByteOffset::new(u64::MAX))
        );
        assert_eq!(
            ByteOffset::new(1).checked_sub(ByteCount::new(1)),
            Ok(ByteOffset::new(0))
        );
    }

    #[test]
    fn offset_arithmetic_rejects_overflow_and_underflow() {
        assert_eq!(
            ByteOffset::new(u64::MAX).checked_add(ByteCount::new(1)),
            Err(Error::Arithmetic {
                operation: "offset addition"
            })
        );
        assert_eq!(
            ByteOffset::new(0).checked_sub(ByteCount::new(1)),
            Err(Error::Arithmetic {
                operation: "offset subtraction"
            })
        );
    }

    #[test]
    fn count_arithmetic_accepts_exact_boundaries() {
        assert_eq!(
            ByteCount::new(u64::MAX - 1).checked_add(ByteCount::new(1)),
            Ok(ByteCount::new(u64::MAX))
        );
        assert_eq!(
            ByteCount::new(1).checked_sub(ByteCount::new(1)),
            Ok(ByteCount::new(0))
        );
    }

    #[test]
    fn count_arithmetic_rejects_overflow_and_underflow() {
        assert_eq!(
            ByteCount::new(u64::MAX).checked_add(ByteCount::new(1)),
            Err(Error::Arithmetic {
                operation: "byte-count addition"
            })
        );
        assert_eq!(
            ByteCount::new(0).checked_sub(ByteCount::new(1)),
            Err(Error::Arithmetic {
                operation: "byte-count subtraction"
            })
        );
    }

    #[test]
    fn native_sized_values_round_trip() {
        let offset = ByteOffset::new(usize::MAX as u64);
        let count = ByteCount::from_usize(usize::MAX);
        assert_eq!(offset.to_usize(), Ok(usize::MAX));
        assert_eq!(count, Ok(ByteCount::new(usize::MAX as u64)));
        assert_eq!(count.and_then(ByteCount::to_usize), Ok(usize::MAX));
    }

    #[cfg(target_pointer_width = "32")]
    #[test]
    fn slice_index_conversion_rejects_values_above_usize() {
        let too_large = u64::from(u32::MAX) + 1;
        assert!(matches!(
            ByteOffset::new(too_large).to_usize(),
            Err(Error::IntegerConversion {
                target: "usize",
                ..
            })
        ));
        assert!(matches!(
            ByteCount::new(too_large).to_usize(),
            Err(Error::IntegerConversion {
                target: "usize",
                ..
            })
        ));
    }
}
