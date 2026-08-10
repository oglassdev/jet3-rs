//! Checked fixed-size page geometry without format-specific constants.

use crate::{ByteCount, ByteOffset, Error, ReadAt};

/// A zero-based page index.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct PageNumber(u64);

impl PageNumber {
    /// Creates a page number without validating it against a source.
    #[must_use]
    pub const fn new(value: u64) -> Self {
        Self(value)
    }

    /// Returns the underlying zero-based page index.
    #[must_use]
    pub const fn get(self) -> u64 {
        self.0
    }

    /// Converts a native-sized index into a page number.
    pub fn from_usize(value: usize) -> Result<Self, Error> {
        u64::try_from(value)
            .map(Self)
            .map_err(|_| Error::IntegerConversion {
                value: value as u128,
                target: "PageNumber",
            })
    }

    /// Converts this page number to a native-sized index.
    pub fn to_usize(self) -> Result<usize, Error> {
        usize::try_from(self.0).map_err(|_| Error::IntegerConversion {
            value: u128::from(self.0),
            target: "usize",
        })
    }
}

impl TryFrom<u128> for PageNumber {
    type Error = Error;

    fn try_from(value: u128) -> Result<Self, Self::Error> {
        u64::try_from(value)
            .map(Self)
            .map_err(|_| Error::IntegerConversion {
                value,
                target: "PageNumber",
            })
    }
}

/// A zero-based byte position within one page.
///
/// Construction is format-neutral and does not know a page size. Pass the
/// value to [`PageGeometry::byte_offset`] to validate it in context.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct PageOffset(u64);

impl PageOffset {
    /// Creates a page-local offset without validating it against a page size.
    #[must_use]
    pub const fn new(value: u64) -> Self {
        Self(value)
    }

    /// Returns the underlying page-local byte position.
    #[must_use]
    pub const fn get(self) -> u64 {
        self.0
    }

    /// Converts a native-sized position into a page-local offset.
    pub fn from_usize(value: usize) -> Result<Self, Error> {
        u64::try_from(value)
            .map(Self)
            .map_err(|_| Error::IntegerConversion {
                value: value as u128,
                target: "PageOffset",
            })
    }

    /// Converts this page-local offset to a native-sized index.
    pub fn to_usize(self) -> Result<usize, Error> {
        usize::try_from(self.0).map_err(|_| Error::IntegerConversion {
            value: u128::from(self.0),
            target: "usize",
        })
    }
}

impl TryFrom<u128> for PageOffset {
    type Error = Error;

    fn try_from(value: u128) -> Result<Self, Self::Error> {
        u64::try_from(value)
            .map(Self)
            .map_err(|_| Error::IntegerConversion {
                value,
                target: "PageOffset",
            })
    }
}

/// Geometry for a captured byte length divided into equal, complete pages.
///
/// This type only performs arithmetic and reference validation. It never
/// reads from a source or charges a work budget.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PageGeometry {
    source_len: ByteCount,
    page_size: ByteCount,
    page_count: u64,
}

impl PageGeometry {
    /// Derives geometry from an explicit nonzero page size.
    ///
    /// Empty input is valid and contains zero pages. Non-empty and empty input
    /// alike must be an exact multiple of `page_size`.
    pub fn new(source_len: ByteCount, page_size: ByteCount) -> Result<Self, Error> {
        if page_size.get() == 0 {
            return Err(Error::InvalidPageSize { page_size });
        }
        let trailing = ByteCount::new(source_len.get() % page_size.get());
        if trailing.get() != 0 {
            return Err(Error::PartialPage {
                input_len: source_len,
                page_size,
                trailing,
            });
        }
        Ok(Self {
            source_len,
            page_size,
            page_count: source_len.get() / page_size.get(),
        })
    }

    /// Captures a source's stable length without reading from it.
    pub fn for_source<S>(source: &S, page_size: ByteCount) -> Result<Self, Error>
    where
        S: ReadAt + ?Sized,
    {
        Self::new(source.len(), page_size)
    }

    /// Returns the captured source length.
    #[must_use]
    pub const fn source_len(self) -> ByteCount {
        self.source_len
    }

    /// Returns the explicit size of one page.
    #[must_use]
    pub const fn page_size(self) -> ByteCount {
        self.page_size
    }

    /// Returns the number of complete pages in the captured source.
    #[must_use]
    pub const fn page_count(self) -> u64 {
        self.page_count
    }

    /// Returns whether `page` identifies a complete captured page.
    #[must_use]
    pub const fn contains(self, page: PageNumber) -> bool {
        page.get() < self.page_count
    }

    /// Validates a page reference against the captured page count.
    pub fn validate_reference(self, page: PageNumber) -> Result<(), Error> {
        if self.contains(page) {
            Ok(())
        } else {
            Err(Error::PageOutOfBounds {
                page: page.get(),
                page_count: self.page_count,
            })
        }
    }

    /// Maps one valid page to its absolute start and byte length.
    pub fn page_byte_range(self, page: PageNumber) -> Result<(ByteOffset, ByteCount), Error> {
        self.validate_reference(page)?;
        let start = checked_page_start(page, self.page_size)?;
        let end = start.checked_add(self.page_size)?;
        if end.get() > self.source_len.get() {
            return Err(Error::UnexpectedEnd {
                offset: start,
                needed: self.page_size,
                available: ByteCount::new(self.source_len.get().saturating_sub(start.get())),
            });
        }
        Ok((start, self.page_size))
    }

    /// Maps a valid page and page-local offset to an absolute byte position.
    pub fn byte_offset(self, page: PageNumber, offset: PageOffset) -> Result<ByteOffset, Error> {
        let (start, _) = self.page_byte_range(page)?;
        if offset.get() >= self.page_size.get() {
            return Err(Error::PageOffsetOutOfBounds {
                offset: offset.get(),
                page_size: self.page_size,
            });
        }
        checked_absolute_offset(start, offset)
    }
}

fn checked_page_start(page: PageNumber, page_size: ByteCount) -> Result<ByteOffset, Error> {
    page.get()
        .checked_mul(page_size.get())
        .map(ByteOffset::new)
        .ok_or(Error::Arithmetic {
            operation: "page-number to byte-offset multiplication",
        })
}

fn checked_absolute_offset(
    page_start: ByteOffset,
    page_offset: PageOffset,
) -> Result<ByteOffset, Error> {
    page_start
        .get()
        .checked_add(page_offset.get())
        .map(ByteOffset::new)
        .ok_or(Error::Arithmetic {
            operation: "page-local to absolute offset addition",
        })
}

#[cfg(test)]
mod tests {
    use std::cell::Cell;

    use super::{
        PageGeometry, PageNumber, PageOffset, checked_absolute_offset, checked_page_start,
    };
    use crate::limits::ReadBudget;
    use crate::{ByteCount, ByteOffset, Error, ReadAt};

    type TestResult = Result<(), Box<dyn std::error::Error>>;

    #[derive(Debug)]
    struct LengthOnlySource {
        len: ByteCount,
        reads: Cell<u64>,
    }

    impl ReadAt for LengthOnlySource {
        fn len(&self) -> ByteCount {
            self.len
        }

        fn read_exact_at(
            &mut self,
            _offset: ByteOffset,
            _destination: &mut [u8],
            _budget: &mut ReadBudget,
        ) -> Result<(), Error> {
            self.reads.set(self.reads.get().saturating_add(1));
            Err(Error::Arithmetic {
                operation: "unexpected read in page geometry test",
            })
        }
    }

    #[test]
    fn typed_page_values_preserve_exact_boundaries() {
        assert_eq!(PageNumber::new(0).get(), 0);
        assert_eq!(PageNumber::new(u64::MAX).get(), u64::MAX);
        assert_eq!(PageOffset::new(0).get(), 0);
        assert_eq!(PageOffset::new(u64::MAX).get(), u64::MAX);
        assert_eq!(
            PageNumber::from_usize(usize::MAX).and_then(PageNumber::to_usize),
            Ok(usize::MAX)
        );
        assert_eq!(
            PageOffset::from_usize(usize::MAX).and_then(PageOffset::to_usize),
            Ok(usize::MAX)
        );
    }

    #[test]
    fn typed_page_values_reject_conversion_overflow() {
        assert_eq!(
            PageNumber::try_from(u128::from(u64::MAX) + 1),
            Err(Error::IntegerConversion {
                value: u128::from(u64::MAX) + 1,
                target: "PageNumber",
            })
        );
        assert_eq!(
            PageOffset::try_from(u128::from(u64::MAX) + 1),
            Err(Error::IntegerConversion {
                value: u128::from(u64::MAX) + 1,
                target: "PageOffset",
            })
        );
    }

    #[test]
    fn zero_page_size_is_rejected_for_empty_and_nonempty_inputs() {
        for input_len in [0, 1] {
            assert_eq!(
                PageGeometry::new(ByteCount::new(input_len), ByteCount::new(0)),
                Err(Error::InvalidPageSize {
                    page_size: ByteCount::new(0),
                })
            );
        }
    }

    #[test]
    fn exact_page_boundaries_derive_counts() {
        let cases = [(0, 4, 0), (4, 4, 1), (8, 4, 2), (u64::MAX, u64::MAX, 1)];
        for (input_len, page_size, expected_count) in cases {
            let geometry = PageGeometry::new(ByteCount::new(input_len), ByteCount::new(page_size));
            assert_eq!(geometry.map(PageGeometry::page_count), Ok(expected_count));
        }
    }

    #[test]
    fn one_below_and_one_above_exact_boundaries_are_partial() {
        let cases = [(3, 3), (5, 1), (7, 3), (9, 1)];
        for (input_len, trailing) in cases {
            assert_eq!(
                PageGeometry::new(ByteCount::new(input_len), ByteCount::new(4)),
                Err(Error::PartialPage {
                    input_len: ByteCount::new(input_len),
                    page_size: ByteCount::new(4),
                    trailing: ByteCount::new(trailing),
                })
            );
        }
    }

    #[test]
    fn source_constructor_reads_only_captured_length() {
        let source = LengthOnlySource {
            len: ByteCount::new(8),
            reads: Cell::new(0),
        };
        let geometry = PageGeometry::for_source(&source, ByteCount::new(4));
        assert_eq!(
            geometry,
            Ok(PageGeometry {
                source_len: ByteCount::new(8),
                page_size: ByteCount::new(4),
                page_count: 2,
            })
        );
        assert_eq!(source.reads.get(), 0);
    }

    #[test]
    fn page_ranges_cover_first_and_last_exactly() -> TestResult {
        let geometry = PageGeometry::new(ByteCount::new(12), ByteCount::new(4))?;
        assert_eq!(
            geometry.page_byte_range(PageNumber::new(0)),
            Ok((ByteOffset::new(0), ByteCount::new(4)))
        );
        assert_eq!(
            geometry.page_byte_range(PageNumber::new(2)),
            Ok((ByteOffset::new(8), ByteCount::new(4)))
        );
        assert!(geometry.contains(PageNumber::new(2)));
        Ok(())
    }

    #[test]
    fn references_at_count_and_one_above_are_rejected() -> TestResult {
        let geometry = PageGeometry::new(ByteCount::new(8), ByteCount::new(4))?;
        for page in [2, 3] {
            assert_eq!(
                geometry.page_byte_range(PageNumber::new(page)),
                Err(Error::PageOutOfBounds {
                    page,
                    page_count: 2,
                })
            );
        }
        Ok(())
    }

    #[test]
    fn empty_geometry_rejects_first_reference() -> TestResult {
        let geometry = PageGeometry::new(ByteCount::new(0), ByteCount::new(4))?;
        assert_eq!(
            geometry.validate_reference(PageNumber::new(0)),
            Err(Error::PageOutOfBounds {
                page: 0,
                page_count: 0,
            })
        );
        Ok(())
    }

    #[test]
    fn local_offsets_accept_last_byte_and_reject_exact_end_and_one_above() -> TestResult {
        let geometry = PageGeometry::new(ByteCount::new(8), ByteCount::new(4))?;
        assert_eq!(
            geometry.byte_offset(PageNumber::new(1), PageOffset::new(3)),
            Ok(ByteOffset::new(7))
        );
        for offset in [4, 5] {
            assert_eq!(
                geometry.byte_offset(PageNumber::new(1), PageOffset::new(offset)),
                Err(Error::PageOffsetOutOfBounds {
                    offset,
                    page_size: ByteCount::new(4),
                })
            );
        }
        Ok(())
    }

    #[test]
    fn page_start_multiplication_overflow_is_structured() {
        assert_eq!(
            checked_page_start(PageNumber::new(u64::MAX), ByteCount::new(2)),
            Err(Error::Arithmetic {
                operation: "page-number to byte-offset multiplication",
            })
        );
    }

    #[test]
    fn absolute_offset_addition_overflow_is_structured() {
        assert_eq!(
            checked_absolute_offset(ByteOffset::new(u64::MAX), PageOffset::new(1)),
            Err(Error::Arithmetic {
                operation: "page-local to absolute offset addition",
            })
        );
    }
}
