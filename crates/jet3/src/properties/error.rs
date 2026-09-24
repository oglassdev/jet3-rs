//! Failures while reading the EXP-0266/0283 named column-property payloads.
use crate::{
    AllocationMapError, CatalogError, Error, LongValueError, RowDirectoryError, RowError,
    TableDefinitionError, UsageMapError, ValueError, WriteError,
};

/// A named column-property payload or its storage could not be read.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("column properties: {self:?}")]
#[non_exhaustive]
pub enum ColumnPropertyError {
    /// Property framing, references or storage invariants are inconsistent.
    Invalid(&'static str),
    /// A resource limit or raw read failed.
    Resource(#[from] Error),
    /// The catalog could not be traversed.
    Catalog(#[from] CatalogError),
    /// A definition needed to check property ownership could not be read.
    Definition(#[from] TableDefinitionError),
    /// A catalog row could not be read.
    Rows(#[from] RowError),
    /// A property storage row directory could not be read.
    Directory(#[source] RowDirectoryError),
    /// A catalog property value could not be decoded.
    Value(#[from] ValueError),
    /// A property payload chain could not be read.
    LongValue(#[from] LongValueError),
    /// An ownership map row could not be read.
    UsageMap(#[source] UsageMapError),
    /// An allocation map could not be read.
    Allocation(#[source] AllocationMapError),
}

impl ColumnPropertyError {
    /// Maps a failure of the shared allocation-map checks that verify property
    /// ownership onto the property reader's variants.
    pub(crate) fn from_ownership_check(source: WriteError) -> Self {
        match source {
            WriteError::Resource(source) => Self::Resource(source),
            WriteError::Catalog(source) => Self::Catalog(source),
            WriteError::Definition(source) => Self::Definition(source),
            WriteError::Rows(source) => Self::Rows(source),
            WriteError::Directory(source) => Self::Directory(source),
            WriteError::Value(source) => Self::Value(source),
            WriteError::LongValue(source) => Self::LongValue(source),
            WriteError::UsageMap(source) => Self::UsageMap(source),
            WriteError::Allocation(source) => Self::Allocation(source),
            WriteError::ColumnProperties(source) => source,
            WriteError::NotFound(detail)
            | WriteError::Unsupported(detail)
            | WriteError::Mismatch(detail) => Self::Invalid(detail),
            _ => Self::Invalid("unexpected property storage reader failure"),
        }
    }
}
