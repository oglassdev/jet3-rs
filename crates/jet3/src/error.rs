//! Structured failures returned by checked, format-neutral primitives.

use std::fmt;

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

/// An error produced while checking binary input or byte arithmetic.
///
/// Variants retain the relevant positions and sizes so callers can report
/// malformed input without parsing error strings.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum Error {
    /// An integer cannot be represented by the requested target type.
    IntegerConversion {
        /// The value that could not be represented.
        value: u128,
        /// A stable description of the target type.
        target: &'static str,
    },
    /// Checked byte arithmetic overflowed or underflowed.
    Arithmetic {
        /// The operation that failed.
        operation: &'static str,
    },
    /// A requested position is outside the input, including one byte past it.
    OffsetOutOfBounds {
        /// Requested absolute position.
        offset: ByteOffset,
        /// Total input length.
        input_len: ByteCount,
    },
    /// The input ended before a complete value could be read.
    UnexpectedEnd {
        /// Position at which the read was attempted.
        offset: ByteOffset,
        /// Number of bytes requested.
        needed: ByteCount,
        /// Bytes remaining at the attempted position.
        available: ByteCount,
    },
    /// A file or other I/O source returned fewer bytes than requested.
    ShortRead {
        /// Absolute source position at which the read began.
        offset: ByteOffset,
        /// Number of bytes requested.
        needed: ByteCount,
        /// Number of bytes actually returned.
        actual: ByteCount,
    },
    /// An underlying I/O operation failed.
    Io {
        /// Stable description of the operation being attempted.
        operation: &'static str,
        /// Portable category reported by the standard library.
        kind: std::io::ErrorKind,
    },
    /// A request exceeded an explicit resource limit.
    LimitExceeded {
        /// Policy limit that rejected the request.
        kind: LimitKind,
        /// Amount requested or accumulated.
        requested: ByteCount,
        /// Configured maximum.
        maximum: ByteCount,
    },
}

impl fmt::Display for Error {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::IntegerConversion { value, target } => {
                write!(formatter, "value {value} cannot be represented as {target}")
            }
            Self::Arithmetic { operation } => {
                write!(
                    formatter,
                    "checked byte arithmetic failed during {operation}"
                )
            }
            Self::OffsetOutOfBounds { offset, input_len } => write!(
                formatter,
                "byte offset {} is outside input of {} bytes",
                offset.get(),
                input_len.get()
            ),
            Self::UnexpectedEnd {
                offset,
                needed,
                available,
            } => write!(
                formatter,
                "input ended at byte {}: needed {} bytes, but {} remain",
                offset.get(),
                needed.get(),
                available.get()
            ),
            Self::ShortRead {
                offset,
                needed,
                actual,
            } => write!(
                formatter,
                "short read at byte {}: needed {} bytes, but received {}",
                offset.get(),
                needed.get(),
                actual.get()
            ),
            Self::Io { operation, kind } => {
                write!(formatter, "I/O failure during {operation}: {kind}")
            }
            Self::LimitExceeded {
                kind,
                requested,
                maximum,
            } => write!(
                formatter,
                "{kind:?} limit exceeded: requested {} bytes, maximum is {}",
                requested.get(),
                maximum.get()
            ),
        }
    }
}

impl std::error::Error for Error {}

#[cfg(test)]
mod tests {
    use super::{Error, LimitKind};
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
        ];

        for (error, expected) in cases {
            assert!(error.to_string().contains(expected));
        }
    }
}
