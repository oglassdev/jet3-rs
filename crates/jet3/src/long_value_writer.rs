//! Encoders for Jet 3 long-value headers and `LVAL` payload rows, the inverse
//! of `long_value.rs` (`EXP-0061`).
//!
//! `EXP-0061` observed a 12-byte header whose first little-endian `u32` holds
//! the 24-bit payload length and exactly one storage flag; inline headers
//! carry eight zero bytes then the payload, and external headers carry a row
//! slot plus three-byte little-endian page at `[4,8)` and four zero bytes at
//! `[8,12)`. External rows live on tag-`01` pages owned by ASCII `LVAL`: a
//! single-page row is exactly the payload, and each chained row is a
//! slot-plus-page pointer to the next row, all zero at the end of the chain,
//! followed by a payload fragment.
//!
//! `EXP-0061` observed inline, single-page, and chained storage at controlled
//! lengths only and established no universal threshold between them, so
//! nothing here chooses a storage class: the caller does, and this module
//! refuses a payload its chosen class cannot hold.

#![allow(
    dead_code,
    reason = "crate-private writer slice awaiting DAO validation"
)]

use std::fmt;

use crate::data_page_directory::MAX_STORED_ROW_LEN;
use crate::{ExternalLongValueStorage, PageNumber, RowLocator};

/// Length of every long-value header.
pub(crate) const HEADER_LEN: usize = 12;
/// Bytes of a chained row that point at the next row.
const CHAIN_POINTER_LEN: usize = 4;
/// Largest payload one single-page `LVAL` row holds.
pub(crate) const MAX_SINGLE_PAGE_PAYLOAD: usize = MAX_STORED_ROW_LEN;
/// Largest fragment one chained `LVAL` row holds after its pointer.
///
/// `EXP-0061` observed exactly this fragment size, 2,032 bytes, on every
/// non-final chained row of its 2,048- and 4,096-byte controls.
pub(crate) const MAX_CHAINED_FRAGMENT: usize = MAX_STORED_ROW_LEN - CHAIN_POINTER_LEN;
/// Largest length the 24-bit header field declares.
const MAX_DECLARED_LENGTH: usize = 0x00ff_ffff;
/// Largest page a three-byte locator names.
const MAX_LOCATOR_PAGE: u64 = 0x00ff_ffff;
const INLINE_FLAG: u32 = 0x8000_0000;
const SINGLE_PAGE_FLAG: u32 = 0x4000_0000;
const CHAINED_FLAG: u32 = 0;

/// Structured failure while encoding a long value.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum LongValueWriteError {
    /// The payload exceeds what its storage class declares or holds.
    PayloadTooLong {
        /// Payload length.
        length: usize,
        /// Largest length the class accepts.
        maximum: usize,
    },
    /// An external header or row would carry no payload bytes.
    EmptyRow,
    /// A chained fragment exceeds one row.
    FragmentTooLong {
        /// Fragment length.
        length: usize,
        /// Largest fragment one row holds.
        maximum: usize,
    },
    /// A row locator names a page above the three-byte range.
    LocatorNotRepresentable {
        /// The unrepresentable locator.
        locator: RowLocator,
    },
    /// An external header would carry the null locator the reader rejects.
    NullTarget,
    /// The output slice cannot hold the encoding.
    OutputTooSmall {
        /// Bytes the encoding needs.
        needed: usize,
        /// Bytes the caller supplied.
        available: usize,
    },
}

impl fmt::Display for LongValueWriteError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "long-value encoding failed: {self:?}")
    }
}

impl std::error::Error for LongValueWriteError {}

/// Encodes an inline long value, header then payload, into `output`.
///
/// Returns the number of bytes written.
pub(crate) fn encode_inline_long_value(
    payload: &[u8],
    output: &mut [u8],
) -> Result<usize, LongValueWriteError> {
    let control = control_word(payload.len(), INLINE_FLAG, MAX_DECLARED_LENGTH)?;
    let needed = HEADER_LEN + payload.len();
    if output.len() < needed {
        return Err(LongValueWriteError::OutputTooSmall {
            needed,
            available: output.len(),
        });
    }
    output[..4].copy_from_slice(&control.to_le_bytes());
    output[4..HEADER_LEN].fill(0);
    output[HEADER_LEN..needed].copy_from_slice(payload);
    Ok(needed)
}

/// Returns the 12-byte header referencing an external payload of `length`
/// bytes whose first row is `target`.
pub(crate) fn external_long_value_header(
    length: usize,
    storage: ExternalLongValueStorage,
    target: RowLocator,
) -> Result<[u8; HEADER_LEN], LongValueWriteError> {
    let (flag, maximum) = match storage {
        ExternalLongValueStorage::SinglePage => (SINGLE_PAGE_FLAG, MAX_SINGLE_PAGE_PAYLOAD),
        ExternalLongValueStorage::Chained => (CHAINED_FLAG, MAX_DECLARED_LENGTH),
    };
    // Every external row holds at least one byte, so no row can back a
    // zero-length header and the reader would reject the pair.
    if length == 0 {
        return Err(LongValueWriteError::EmptyRow);
    }
    let control = control_word(length, flag, maximum)?;
    if is_null(target) {
        return Err(LongValueWriteError::NullTarget);
    }
    let mut header = [0_u8; HEADER_LEN];
    header[..4].copy_from_slice(&control.to_le_bytes());
    header[4..8].copy_from_slice(&encode_locator(target)?);
    Ok(header)
}

/// Checks that `payload` can be one single-page `LVAL` row, which is exactly
/// the payload bytes.
pub(crate) fn validate_single_page_row(payload: &[u8]) -> Result<(), LongValueWriteError> {
    if payload.is_empty() {
        return Err(LongValueWriteError::EmptyRow);
    }
    if payload.len() > MAX_SINGLE_PAGE_PAYLOAD {
        return Err(LongValueWriteError::PayloadTooLong {
            length: payload.len(),
            maximum: MAX_SINGLE_PAGE_PAYLOAD,
        });
    }
    Ok(())
}

/// Splits `payload` into the fragments of a chain, largest first.
///
/// This is the split `EXP-0061` observed: every row but the last carries the
/// largest fragment a row holds, and the last carries the remainder.
pub(crate) fn chained_fragments(payload: &[u8]) -> impl ExactSizeIterator<Item = &[u8]> {
    payload.chunks(MAX_CHAINED_FRAGMENT)
}

/// Encodes one chained `LVAL` row, pointer then fragment, into `output`.
///
/// `next` is the following row, or `None` for the last row of the chain.
/// Returns the number of bytes written.
pub(crate) fn encode_chained_row(
    fragment: &[u8],
    next: Option<RowLocator>,
    output: &mut [u8],
) -> Result<usize, LongValueWriteError> {
    if fragment.is_empty() {
        return Err(LongValueWriteError::EmptyRow);
    }
    if fragment.len() > MAX_CHAINED_FRAGMENT {
        return Err(LongValueWriteError::FragmentTooLong {
            length: fragment.len(),
            maximum: MAX_CHAINED_FRAGMENT,
        });
    }
    let pointer = match next {
        // The null locator is the end-of-chain marker, so naming it as a
        // following row would silently truncate the chain.
        Some(locator) if is_null(locator) => return Err(LongValueWriteError::NullTarget),
        Some(locator) => encode_locator(locator)?,
        None => [0; CHAIN_POINTER_LEN],
    };
    let needed = CHAIN_POINTER_LEN + fragment.len();
    if output.len() < needed {
        return Err(LongValueWriteError::OutputTooSmall {
            needed,
            available: output.len(),
        });
    }
    output[..CHAIN_POINTER_LEN].copy_from_slice(&pointer);
    output[CHAIN_POINTER_LEN..needed].copy_from_slice(fragment);
    Ok(needed)
}

/// Returns the header control word: the 24-bit `length` plus one flag.
fn control_word(length: usize, flag: u32, maximum: usize) -> Result<u32, LongValueWriteError> {
    if length > maximum {
        return Err(LongValueWriteError::PayloadTooLong { length, maximum });
    }
    // `maximum` never exceeds the 24-bit field, so this cannot truncate.
    Ok(flag | length as u32)
}

/// Returns the slot byte then three-byte little-endian page of `locator`.
fn encode_locator(locator: RowLocator) -> Result<[u8; CHAIN_POINTER_LEN], LongValueWriteError> {
    let page = locator.page().get();
    if page > MAX_LOCATOR_PAGE {
        return Err(LongValueWriteError::LocatorNotRepresentable { locator });
    }
    let [low, mid, high, _] = (page as u32).to_le_bytes();
    Ok([locator.slot(), low, mid, high])
}

/// Returns the null-locator sentinel the reader treats as end of chain.
pub(crate) const fn null_locator() -> RowLocator {
    RowLocator::new(PageNumber::new(0), 0)
}

fn is_null(locator: RowLocator) -> bool {
    locator.page().get() == 0 && locator.slot() == 0
}

#[cfg(test)]
#[path = "long_value_writer_tests.rs"]
mod tests;
