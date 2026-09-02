//! Crate-private `MSysObjects` `ParentId`/`Name` index key encoding.
//!
//! The key grammar and the ASCII primary weight map both come from `EXP-0087`,
//! which observed them over 90 probed CP1252 bytes at two interior positions in
//! both orderings across three fresh replicas. The Long component follows
//! `EXP-0062`.
//!
//! `EXP-0087` deliberately derives no weight, expansion rule, or secondary
//! assignment for name bytes above `0x7E`, so this module rejects them with a
//! structured error rather than guessing.

#![allow(
    dead_code,
    reason = "crate-private writer slice awaiting DAO validation"
)]

use std::fmt;

/// Lowest name byte `EXP-0087` established a primary weight for.
const FIRST_MAPPED_BYTE: u8 = 0x20;
/// Highest name byte `EXP-0087` established a primary weight for.
const LAST_MAPPED_BYTE: u8 = 0x7e;

/// Primary weight per name byte from `FIRST_MAPPED_BYTE`, `0` where unmapped.
///
/// `EXP-0087` observed no weight of `0` and no byte whose weight has a zero
/// high nibble, so `0` is an unambiguous unmapped marker. The gaps are the five
/// bytes Access rejects in object names, which therefore never reach a key.
const PRIMARY_WEIGHTS: [u8; 95] = [
    0x11, 0x00, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18, // 20..27  ' !"#$%&\''
    0x19, 0x1a, 0x1b, 0x1c, 0x1d, 0x1e, 0x00, 0x20, // 28..2f  '()*+,-./'
    0x56, 0x57, 0x58, 0x59, 0x5a, 0x5b, 0x5c, 0x5d, // 30..37  '01234567'
    0x5e, 0x5f, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, // 38..3f  '89:;<=>?'
    0x27, 0x60, 0x61, 0x62, 0x64, 0x66, 0x67, 0x68, // 40..47  '@ABCDEFG'
    0x69, 0x6a, 0x6b, 0x6c, 0x6d, 0x6f, 0x70, 0x72, // 48..4f  'HIJKLMNO'
    0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x7a, 0x7b, // 50..57  'PQRSTUVW'
    0x7c, 0x7d, 0x7e, 0x00, 0x29, 0x00, 0x2b, 0x2c, // 58..5f  'XYZ[\\]^_'
    0x00, 0x60, 0x61, 0x62, 0x64, 0x66, 0x67, 0x68, // 60..67  '`abcdefg'
    0x69, 0x6a, 0x6b, 0x6c, 0x6d, 0x6f, 0x70, 0x72, // 68..6f  'hijklmno'
    0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x7a, 0x7b, // 70..77  'pqrstuvw'
    0x7c, 0x7d, 0x7e, 0x2e, 0x2f, 0x30, 0x31, // 78..7e  'xyz{|}~'
];

/// Marker `EXP-0062` observed before each non-null key component.
const COMPONENT_MARKER: u8 = 0x7f;
/// Encoded length of the leading non-null Long `ParentId` component.
const LONG_COMPONENT_LEN: usize = 5;
/// The text component's marker plus its terminating nibble-stream byte.
const TEXT_COMPONENT_OVERHEAD: usize = 2;

/// Structured failure while encoding a `ParentId`/`Name` index key.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum CatalogNameKeyError {
    /// The name is empty, so it has no text component to encode.
    EmptyName,
    /// `EXP-0087` establishes no primary weight for this name byte.
    UnmappedNameByte {
        /// Zero-based position of the rejected byte within the name.
        position: usize,
        /// The rejected name byte.
        byte: u8,
    },
    /// The encoded key does not fit the caller's buffer.
    KeyTooLong {
        /// Bytes the encoded key needs.
        needed: usize,
        /// Bytes the caller supplied.
        available: usize,
    },
}

impl fmt::Display for CatalogNameKeyError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::EmptyName => formatter.write_str("catalog name key needs a non-empty name"),
            Self::UnmappedNameByte { position, byte } => write!(
                formatter,
                "name byte {byte:#04x} at position {position} has no established primary weight"
            ),
            Self::KeyTooLong { needed, available } => write!(
                formatter,
                "catalog name key needs {needed} bytes but {available} are available"
            ),
        }
    }
}

impl std::error::Error for CatalogNameKeyError {}

/// Returns the encoded length of the key for `name`, or `None` if it is empty.
///
/// `EXP-0087` observed exactly one primary weight per mapped name byte, so the
/// length depends only on the name's length.
pub(crate) const fn catalog_name_key_len(name: &[u8]) -> Option<usize> {
    if name.is_empty() {
        None
    } else {
        Some(LONG_COMPONENT_LEN + TEXT_COMPONENT_OVERHEAD + name.len())
    }
}

/// Encodes the `ParentId`/`Name` key for one catalog row into `output`.
///
/// Returns the number of bytes written. The key is the `EXP-0062` non-null Long
/// encoding of `parent`, then the component marker, then one `EXP-0087` primary
/// weight per name byte, then the nibble stream, which for a name carrying no
/// secondary weights is a single zero byte: one leading zero nibble, no
/// secondary nibbles, one terminating zero nibble.
pub(crate) fn encode_catalog_name_key(
    parent: i32,
    name: &[u8],
    output: &mut [u8],
) -> Result<usize, CatalogNameKeyError> {
    let needed = catalog_name_key_len(name).ok_or(CatalogNameKeyError::EmptyName)?;
    let available = output.len();
    if needed > available {
        return Err(CatalogNameKeyError::KeyTooLong { needed, available });
    }
    // Resolve every weight before writing so a refused name leaves no partial key.
    for (position, &byte) in name.iter().enumerate() {
        if primary_weight(byte).is_none() {
            return Err(CatalogNameKeyError::UnmappedNameByte { position, byte });
        }
    }
    output[0] = COMPONENT_MARKER;
    output[1..LONG_COMPONENT_LEN].copy_from_slice(&long_component(parent));
    output[LONG_COMPONENT_LEN] = COMPONENT_MARKER;
    let weights = &mut output[LONG_COMPONENT_LEN + 1..needed - 1];
    for (&byte, slot) in name.iter().zip(weights.iter_mut()) {
        *slot = primary_weight(byte).unwrap_or_default();
    }
    output[needed - 1] = 0;
    Ok(needed)
}

/// Returns the `EXP-0062` non-null Long key body: big-endian with a flipped
/// sign bit so the unsigned byte order matches the signed value order.
fn long_component(value: i32) -> [u8; LONG_COMPONENT_LEN - 1] {
    let mut raw = value.to_be_bytes();
    raw[0] ^= 0x80;
    raw
}

/// Returns the established primary weight for one name byte.
fn primary_weight(byte: u8) -> Option<u8> {
    if !(FIRST_MAPPED_BYTE..=LAST_MAPPED_BYTE).contains(&byte) {
        return None;
    }
    let weight = PRIMARY_WEIGHTS[usize::from(byte - FIRST_MAPPED_BYTE)];
    (weight != 0).then_some(weight)
}

#[cfg(test)]
#[path = "catalog_name_key_tests.rs"]
mod tests;
