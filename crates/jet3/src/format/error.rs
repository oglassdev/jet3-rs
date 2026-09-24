//! Structured failures returned by checked, format-neutral primitives.

use crate::{ByteCount, ByteOffset};

/// Identifies the resource policy that rejected an operation.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LimitKind {
    /// Maximum accepted input length.
    InputBytes,
    /// Maximum length of one requested byte range.
    SingleReadBytes,
    /// Maximum cumulative bytes read by one cursor.
    TotalReadBytes,
}

/// Identifies an operation-wide resource ceiling.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ResourceLimitKind {
    /// Cumulative bytes reserved or allocated.
    AllocationBytes,
    /// Size of one decoded value.
    DecodedValueBytes,
    /// Cumulative bytes produced by value decoding.
    TotalDecodedBytes,
    /// Cumulative bytes encoded into caller-provided output.
    EncodedBytes,
    /// Cumulative count-driven item work.
    ItemWork,
    /// Cumulative page visits.
    PageVisits,
    /// Depth of one followed chain.
    ChainDepth,
    /// Cumulative aggregate non-I/O work.
    TotalWorkUnits,
}

/// An error produced while checking input, arithmetic, or resource policy.
///
/// Variants retain the relevant positions and sizes so callers can report
/// malformed input without parsing error strings.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[non_exhaustive]
pub enum Error {
    /// An integer cannot be represented by the requested target type.
    #[error("value {value} cannot be represented as {target}")]
    IntegerConversion {
        /// The value that could not be represented.
        value: u128,
        /// A stable description of the target type.
        target: &'static str,
    },
    /// Checked byte arithmetic overflowed or underflowed.
    #[error("checked byte arithmetic failed during {operation}")]
    Arithmetic {
        /// The operation that failed.
        operation: &'static str,
    },
    /// A requested position is outside the input, including one byte past it.
    #[error("byte offset {} is outside input of {} bytes", .offset.get(), .input_len.get())]
    OffsetOutOfBounds {
        /// Requested absolute position.
        offset: ByteOffset,
        /// Total input length.
        input_len: ByteCount,
    },
    /// A requested write position is outside the fixed output slice.
    #[error(
        "byte offset {} is outside output capacity of {} bytes",
        .offset.get(),
        .capacity.get()
    )]
    OutputOffsetOutOfBounds {
        /// Requested absolute position.
        offset: ByteOffset,
        /// Total output capacity.
        capacity: ByteCount,
    },
    /// The fixed output slice cannot hold a complete requested write.
    #[error(
        "output capacity exhausted at byte {}: needed {} bytes, but {} remain",
        .offset.get(),
        .needed.get(),
        .available.get()
    )]
    OutputCapacityExceeded {
        /// Position at which the write was attempted.
        offset: ByteOffset,
        /// Number of bytes requested.
        needed: ByteCount,
        /// Bytes remaining at the attempted position.
        available: ByteCount,
    },
    /// The input ended before a complete value could be read.
    #[error(
        "input ended at byte {}: needed {} bytes, but {} remain",
        .offset.get(),
        .needed.get(),
        .available.get()
    )]
    UnexpectedEnd {
        /// Position at which the read was attempted.
        offset: ByteOffset,
        /// Number of bytes requested.
        needed: ByteCount,
        /// Bytes remaining at the attempted position.
        available: ByteCount,
    },
    /// A file or other I/O source returned fewer bytes than requested.
    #[error(
        "short read at byte {}: needed {} bytes, but received {}",
        .offset.get(),
        .needed.get(),
        .actual.get()
    )]
    ShortRead {
        /// Absolute source position at which the read began.
        offset: ByteOffset,
        /// Number of bytes requested.
        needed: ByteCount,
        /// Number of bytes actually returned.
        actual: ByteCount,
    },
    /// An underlying I/O operation failed.
    #[error("I/O failure during {operation}: {kind}")]
    Io {
        /// Stable description of the operation being attempted.
        operation: &'static str,
        /// Portable category reported by the standard library.
        kind: std::io::ErrorKind,
    },
    /// A page size is zero or cannot be used for checked page geometry.
    #[error("invalid page size: {} bytes", .page_size.get())]
    InvalidPageSize {
        /// Rejected page size.
        page_size: ByteCount,
    },
    /// The input contains bytes after its final complete page.
    #[error(
        "input of {} bytes is not divisible into {}-byte pages: {} trailing bytes",
        .input_len.get(),
        .page_size.get(),
        .trailing.get()
    )]
    PartialPage {
        /// Total input length.
        input_len: ByteCount,
        /// Expected size of every complete page.
        page_size: ByteCount,
        /// Bytes present after the final complete page.
        trailing: ByteCount,
    },
    /// A page number is outside the captured page range.
    #[error("page {page} is outside input containing {page_count} pages")]
    PageOutOfBounds {
        /// Requested zero-based page number.
        page: u64,
        /// Number of complete pages in the input.
        page_count: u64,
    },
    /// An offset within a page is at or beyond the page size.
    #[error("page offset {offset} is outside a {}-byte page", .page_size.get())]
    PageOffsetOutOfBounds {
        /// Requested zero-based offset within the page.
        offset: u64,
        /// Size of the page.
        page_size: ByteCount,
    },
    /// A request exceeded an explicit resource limit.
    #[error(
        "{kind:?} limit exceeded: requested {} bytes, maximum is {}",
        .requested.get(),
        .maximum.get()
    )]
    LimitExceeded {
        /// Policy limit that rejected the request.
        kind: LimitKind,
        /// Amount requested or accumulated.
        requested: ByteCount,
        /// Configured maximum.
        maximum: ByteCount,
    },
    /// An operation-wide count or work ceiling was exceeded.
    #[error("{kind:?} resource limit exceeded: requested {requested}, maximum is {maximum}")]
    ResourceLimitExceeded {
        /// Policy dimension that rejected the request.
        kind: ResourceLimitKind,
        /// Prospective cumulative value or checked magnitude.
        requested: u64,
        /// Configured maximum.
        maximum: u64,
    },
}

#[cfg(test)]
mod tests {
    use super::{Error, LimitKind, ResourceLimitKind};
    use crate::{ByteCount, ByteOffset};

    #[test]
    fn display_includes_structured_error_context() {
        let cases = [
            (
                Error::IntegerConversion {
                    value: u128::MAX,
                    target: "usize",
                },
                "usize",
            ),
            (
                Error::Arithmetic {
                    operation: "test addition",
                },
                "test addition",
            ),
            (
                Error::OffsetOutOfBounds {
                    offset: ByteOffset::new(8),
                    input_len: ByteCount::new(7),
                },
                "offset 8",
            ),
            (
                Error::OutputOffsetOutOfBounds {
                    offset: ByteOffset::new(8),
                    capacity: ByteCount::new(7),
                },
                "output capacity",
            ),
            (
                Error::OutputCapacityExceeded {
                    offset: ByteOffset::new(2),
                    needed: ByteCount::new(4),
                    available: ByteCount::new(3),
                },
                "needed 4",
            ),
            (
                Error::UnexpectedEnd {
                    offset: ByteOffset::new(2),
                    needed: ByteCount::new(4),
                    available: ByteCount::new(3),
                },
                "needed 4",
            ),
            (
                Error::LimitExceeded {
                    kind: LimitKind::TotalReadBytes,
                    requested: ByteCount::new(5),
                    maximum: ByteCount::new(4),
                },
                "TotalReadBytes",
            ),
            (
                Error::ShortRead {
                    offset: ByteOffset::new(9),
                    needed: ByteCount::new(3),
                    actual: ByteCount::new(2),
                },
                "received 2",
            ),
            (
                Error::Io {
                    operation: "read source",
                    kind: std::io::ErrorKind::PermissionDenied,
                },
                "permission denied",
            ),
            (
                Error::InvalidPageSize {
                    page_size: ByteCount::new(0),
                },
                "invalid page size",
            ),
            (
                Error::PartialPage {
                    input_len: ByteCount::new(9),
                    page_size: ByteCount::new(4),
                    trailing: ByteCount::new(1),
                },
                "1 trailing",
            ),
            (
                Error::PageOutOfBounds {
                    page: 3,
                    page_count: 3,
                },
                "page 3",
            ),
            (
                Error::PageOffsetOutOfBounds {
                    offset: 4,
                    page_size: ByteCount::new(4),
                },
                "offset 4",
            ),
            (
                Error::ResourceLimitExceeded {
                    kind: ResourceLimitKind::EncodedBytes,
                    requested: 5,
                    maximum: 4,
                },
                "EncodedBytes",
            ),
        ];

        for (error, expected) in cases {
            assert!(error.to_string().contains(expected));
        }
    }
}
