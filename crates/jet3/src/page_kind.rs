//! Experimental classification of complete Jet 3 pages by their first byte.
//!
//! The classification is intentionally narrow. `SRC-0020` documents only the
//! byte-zero tags exposed here; no other page-header byte is inspected or
//! assigned meaning. Unknown and contextually invalid tags remain successful,
//! lossless classifications rather than malformed-input errors.

use std::fmt;

use crate::{Error, JET3_PAGE_SIZE, PageNumber, ResourceBudget};

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;

// Byte-zero page tags documented by SRC-0020.
const DATABASE_DEFINITION_TAG: u8 = 0x00;
const DATA_TAG: u8 = 0x01;
const TABLE_DEFINITION_TAG: u8 = 0x02;
const INTERMEDIATE_INDEX_TAG: u8 = 0x03;
const LEAF_INDEX_TAG: u8 = 0x04;
const EXTENDED_USAGE_BITMAP_TAG: u8 = 0x05;

/// Experimental byte-zero classification of one complete Jet 3 page.
///
/// A named variant reports only the tag and page-number context documented by
/// `SRC-0020`. It does not validate the remaining page header or payload.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[non_exhaustive]
pub enum PageKind {
    /// Page zero whose byte-zero tag is `0x00`.
    DatabaseDefinition,
    /// A nonzero page whose byte-zero tag is `0x01`.
    Data,
    /// A nonzero page whose byte-zero tag is `0x02`.
    TableDefinition,
    /// A nonzero page whose byte-zero tag is `0x03`.
    IntermediateIndex,
    /// A nonzero page whose byte-zero tag is `0x04`.
    LeafIndex,
    /// A nonzero page whose byte-zero tag is `0x05`.
    ExtendedUsageBitmap,
    /// An unsupported tag, including a documented tag in the wrong page
    /// number context.
    Unknown(u8),
}

/// A borrowed, complete page paired with its experimental classification.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ClassifiedPage<'a> {
    number: PageNumber,
    kind: PageKind,
    raw: &'a [u8; PAGE_BYTES],
}

impl<'a> ClassifiedPage<'a> {
    /// Returns the physical page number supplied by the caller.
    #[must_use]
    pub const fn number(&self) -> PageNumber {
        self.number
    }

    /// Returns the byte-zero classification.
    #[must_use]
    pub const fn kind(&self) -> PageKind {
        self.kind
    }

    /// Returns all page bytes exactly as supplied.
    #[must_use]
    pub const fn raw_bytes(&self) -> &'a [u8; PAGE_BYTES] {
        self.raw
    }
}

/// A structured failure while classifying an already-read fixed page.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum PageClassificationError {
    /// The required single classification work unit exceeded the operation
    /// budget.
    Resource(Error),
}

impl fmt::Display for PageClassificationError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Resource(source) => write!(formatter, "page classification rejected: {source}"),
        }
    }
}

impl std::error::Error for PageClassificationError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Resource(source) => Some(source),
        }
    }
}

/// Classifies one already-read page using only its page number and byte zero.
///
/// Exactly one explicit work unit is charged before byte zero is inspected.
/// The page visit and source-read charges, if any, belong to the caller that
/// acquired `raw`. Unsupported values are returned as [`PageKind::Unknown`].
pub fn classify_page<'a>(
    number: PageNumber,
    raw: &'a [u8; PAGE_BYTES],
    budget: &mut ResourceBudget,
) -> Result<ClassifiedPage<'a>, PageClassificationError> {
    budget
        .charge_work_units(1)
        .map_err(PageClassificationError::Resource)?;

    let tag = raw[0];
    let kind = if number.get() == 0 {
        if tag == DATABASE_DEFINITION_TAG {
            PageKind::DatabaseDefinition
        } else {
            PageKind::Unknown(tag)
        }
    } else {
        match tag {
            DATA_TAG => PageKind::Data,
            TABLE_DEFINITION_TAG => PageKind::TableDefinition,
            INTERMEDIATE_INDEX_TAG => PageKind::IntermediateIndex,
            LEAF_INDEX_TAG => PageKind::LeafIndex,
            EXTENDED_USAGE_BITMAP_TAG => PageKind::ExtendedUsageBitmap,
            DATABASE_DEFINITION_TAG | 0x06..=u8::MAX => PageKind::Unknown(tag),
        }
    };

    Ok(ClassifiedPage { number, kind, raw })
}

#[cfg(test)]
#[path = "page_kind_tests.rs"]
mod tests;
