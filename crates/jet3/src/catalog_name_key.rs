//! `MSysObjects` ParentId/Name keys from EXP-0087/0101/0248/0277/0278.
//!
//! The Name field uses the English-US/CP1252 Text transform, including primary
//! expansions and accent nibbles. EXP-0101 records catalog keys for every
//! defined extended byte; EXP-0248 supplies the complete Text transformation.

use std::fmt;

use crate::{IndexDirection, numeric_index_key::MAX_COMPONENT_BYTES};

/// EXP-0062: marker and four-byte signed Long component.
pub(crate) const LONG_COMPONENT_LEN: usize = 5;
/// EXP-0249 permits 64-byte table names; EXP-0248 bounds their Text expansion.
pub(crate) const MAX_CREATION_KEY_BYTES: usize = LONG_COMPONENT_LEN + 3 * 64 + 2;

/// Structured failure while encoding a ParentId/Name index key.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CatalogNameKeyError {
    /// The name is empty.
    EmptyName,
    /// The name byte is outside the supported object-name grammar.
    UnmappedNameByte {
        /// Zero-based byte position in the name.
        position: usize,
        /// Unsupported byte.
        byte: u8,
    },
    /// The name exceeds the EXP-0249 object-name boundary.
    NameTooLong {
        /// Requested byte length.
        length: usize,
        /// Largest encodable byte length.
        maximum: usize,
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
                "unsupported catalog name byte {byte:#04x} at position {position}"
            ),
            Self::NameTooLong { length, maximum } => write!(
                formatter,
                "catalog name has {length} bytes; maximum is {maximum}"
            ),
            Self::KeyTooLong { needed, available } => write!(
                formatter,
                "catalog name key needs {needed} bytes but {available} are available"
            ),
        }
    }
}

impl std::error::Error for CatalogNameKeyError {}

/// EXP-0087 excludes controls and the five punctuation bytes below; SRC-0025
/// identifies undefined CP1252 bytes. EXP-0101 admits every defined extended byte.
pub(crate) fn supported_name_byte(byte: u8) -> bool {
    byte >= 0x20
        && !matches!(
            byte,
            b'!' | b'.' | b'[' | b']' | b'`' | 0x7f | 0x81 | 0x8d | 0x8f | 0x90 | 0x9d
        )
}

pub(crate) fn validate_catalog_name(name: &[u8]) -> Result<(), CatalogNameKeyError> {
    if name.is_empty() {
        return Err(CatalogNameKeyError::EmptyName);
    }
    if name.len() > 64 {
        return Err(CatalogNameKeyError::NameTooLong {
            length: name.len(),
            maximum: 64,
        });
    }
    if name[0] == b' ' {
        return Err(CatalogNameKeyError::UnmappedNameByte {
            position: 0,
            byte: b' ',
        });
    }
    for (position, &byte) in name.iter().enumerate() {
        if !supported_name_byte(byte) {
            return Err(CatalogNameKeyError::UnmappedNameByte { position, byte });
        }
    }
    Ok(())
}

/// Names collide when the unique ParentId/Name catalog key is identical.
pub(crate) fn catalog_names_equal(left: &[u8], right: &[u8]) -> bool {
    if left.len() > 64 || right.len() > 64 {
        return false;
    }
    if left.is_ascii() && right.is_ascii() && !left.ends_with(b" ") && !right.ends_with(b" ") {
        return left.eq_ignore_ascii_case(right);
    }
    match (NameKey::new(left), NameKey::new(right)) {
        (Ok(a), Ok(b)) => a.bytes() == b.bytes(),
        _ => false,
    }
}

/// EXP-0248/0277: collation keys also order logical index names, including
/// generated `.rX` relationship names. User-name grammar is checked separately.
pub(crate) struct NameKey {
    bytes: [u8; MAX_CREATION_KEY_BYTES - LONG_COMPONENT_LEN],
    len: usize,
}

impl NameKey {
    pub(crate) fn new(name: &[u8]) -> Result<Self, CatalogNameKeyError> {
        if name.len() > 64 {
            return Err(CatalogNameKeyError::NameTooLong {
                length: name.len(),
                maximum: 64,
            });
        }
        if let Some((position, &byte)) = name
            .iter()
            .enumerate()
            .find(|(_, byte)| matches!(byte, 0x81 | 0x8d | 0x8f | 0x90 | 0x9d))
        {
            return Err(CatalogNameKeyError::UnmappedNameByte { position, byte });
        }
        let mut component = [0; MAX_COMPONENT_BYTES];
        let len =
            crate::text_index_key::encode(name, 64, IndexDirection::Ascending, &mut component)
                .ok_or(CatalogNameKeyError::NameTooLong {
                    length: name.len(),
                    maximum: 64,
                })?;
        let mut key = Self {
            bytes: [0; MAX_CREATION_KEY_BYTES - LONG_COMPONENT_LEN],
            len,
        };
        key.bytes[..len].copy_from_slice(&component[..len]);
        Ok(key)
    }

    pub(crate) fn bytes(&self) -> &[u8] {
        &self.bytes[..self.len]
    }
}

/// Encodes completely before writing, so every refusal preserves `output`.
pub(crate) fn encode_catalog_name_key(
    parent: i32,
    name: &[u8],
    output: &mut [u8],
) -> Result<usize, CatalogNameKeyError> {
    validate_catalog_name(name)?;
    let component = NameKey::new(name)?;
    let needed = LONG_COMPONENT_LEN + component.len;
    if output.len() < needed {
        return Err(CatalogNameKeyError::KeyTooLong {
            needed,
            available: output.len(),
        });
    }
    output[0] = 0x7f;
    let mut raw = parent.to_be_bytes();
    raw[0] ^= 0x80;
    output[1..LONG_COMPONENT_LEN].copy_from_slice(&raw);
    output[LONG_COMPONENT_LEN..needed].copy_from_slice(component.bytes());
    Ok(needed)
}

#[cfg(test)]
#[path = "catalog_name_key_tests.rs"]
mod tests;
