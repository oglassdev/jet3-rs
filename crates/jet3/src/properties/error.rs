//! Failures while reading the EXP-0266/0283 named column-property payloads.
use crate::{
    AllocationMapError, CatalogError, Error, LongValueError, RowDirectoryError, RowError,
    TableDefinitionError, UsageMapError, ValueError, WriteError,
};
use std::fmt;

/// A named column-property payload or its storage could not be read.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum ColumnPropertyError {
    /// Property framing, references or storage invariants are inconsistent.
    Invalid(&'static str),
    /// A resource limit or raw read failed.
    Resource(Error),
    /// The catalog could not be traversed.
    Catalog(CatalogError),
    /// A definition needed to check property ownership could not be read.
    Definition(TableDefinitionError),
    /// A catalog row could not be read.
    Rows(RowError),
    /// A property storage row directory could not be read.
    Directory(RowDirectoryError),
    /// A catalog property value could not be decoded.
    Value(ValueError),
    /// A property payload chain could not be read.
    LongValue(LongValueError),
    /// An ownership map row could not be read.
    UsageMap(UsageMapError),
    /// An allocation map could not be read.
    Allocation(AllocationMapError),
}

impl fmt::Display for ColumnPropertyError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "column properties: {self:?}")
    }
}

impl std::error::Error for ColumnPropertyError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Invalid(_) => None,
            Self::Resource(source) => Some(source),
            Self::Catalog(source) => Some(source),
            Self::Definition(source) => Some(source),
            Self::Rows(source) => Some(source),
            Self::Directory(source) => Some(source),
            Self::Value(source) => Some(source),
            Self::LongValue(source) => Some(source),
            Self::UsageMap(source) => Some(source),
            Self::Allocation(source) => Some(source),
        }
    }
}

macro_rules! conversion {
    ($source:ty, $variant:ident) => {
        impl From<$source> for ColumnPropertyError {
            fn from(source: $source) -> Self {
                Self::$variant(source)
            }
        }
    };
}
conversion!(Error, Resource);
conversion!(CatalogError, Catalog);
conversion!(TableDefinitionError, Definition);
conversion!(RowError, Rows);
conversion!(ValueError, Value);
conversion!(LongValueError, LongValue);

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
