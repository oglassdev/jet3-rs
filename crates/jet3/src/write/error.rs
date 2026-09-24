//! The shared failure of every write operation.
use std::error::Error as StdError;
use std::fmt;

use crate::ColumnOrdinal;

/// A rejected request, malformed source, exhausted resource policy, or publication failure
/// of an insert, update, delete, schema edit or database creation.
#[derive(Debug)]
#[non_exhaustive]
pub enum WriteError {
    /// A requested table schema is invalid.
    Schema(crate::TableSchemaPlanError),
    /// A table definition could not be encoded.
    DefinitionEncoding(crate::TableDefinitionWriteError),
    /// A schema edit could not encode a data page.
    PageImage(crate::PageImageError),
    /// A schema edit could not encode an allocation map.
    MapEncoding(crate::UsageMapWriteError),
    /// Named column properties or their storage are malformed.
    ColumnProperties(crate::ColumnPropertyError),
    /// The named user table, row, or column was not found.
    NotFound(&'static str),
    /// The request needs an unimplemented update capability.
    Unsupported(&'static str),
    /// Source or private bytes did not match the planned update.
    Mismatch(&'static str),
    /// A non-null child key would have no matching parent after the change.
    RelationshipConstraint {
        /// Definition page of the referenced parent table.
        parent: crate::PageNumber,
        /// Definition page of the referencing child table.
        child: crate::PageNumber,
        /// Child value requiring a matching parent.
        value: i32,
    },
    /// A non-Long scalar or composite child key would have no matching parent after the change.
    ScalarRelationshipConstraint {
        /// Definition page of the referenced parent table.
        parent: crate::PageNumber,
        /// Definition page of the referencing child table.
        child: crate::PageNumber,
    },
    /// A null parent key cannot be changed or removed while null child keys exist.
    NullRelationshipConstraint {
        /// Definition page of the referenced parent table.
        parent: crate::PageNumber,
        /// Definition page of the referencing child table.
        child: crate::PageNumber,
    },
    /// The database uses a sort order outside the six observed single-byte
    /// locales (EXP-0309). Reading remains supported.
    UnsupportedSortOrder {
        /// Raw page-zero sort-order marker.
        raw: [u8; 4],
    },
    /// The table or column stores a ValidationRule; Jet expressions are not evaluated.
    ValidationRule {
        /// The column whose rule applies, or `None` for the table rule.
        column: Option<ColumnOrdinal>,
    },
    /// Resource policy or raw input failure.
    Resource(crate::Error),
    /// File operation failed.
    Io(std::io::Error),
    /// Database header failure.
    Open(crate::DatabaseOpenError),
    /// Catalog failure.
    Catalog(crate::CatalogError),
    /// Table definition failure.
    Definition(crate::TableDefinitionError),
    /// Row traversal failure.
    Rows(crate::RowError),
    /// Row directory failure.
    Directory(crate::RowDirectoryError),
    /// Replacement value or fixed layout failed checked row encoding.
    Encoding(crate::RowWriteError),
    /// Existing indexed numeric value failed decoding.
    Value(crate::ValueError),
    /// Existing external long-value storage failed validation.
    LongValue(crate::LongValueError),
    /// Available map row is malformed.
    UsageMap(crate::UsageMapError),
    /// Allocation bitmap is malformed or exhausted its budget.
    Allocation(crate::AllocationMapError),
    /// Index metadata, tree, or row reference is malformed.
    Index(crate::IndexTreeError),
    /// Atomic publication failed; its stage indicates whether publication occurred.
    Publish(crate::PublishError),
    /// A new database could not be composed; nothing was written.
    Compose(crate::ComposeError),
    /// A composed new database could not be written, checked, or published.
    CreatePublish(crate::PublishError),
}

impl fmt::Display for WriteError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Compose(source) => write!(formatter, "database composition failed: {source}"),
            Self::CreatePublish(source) => {
                write!(formatter, "database publication failed: {source}")
            }
            _ => write!(formatter, "field update failed: {self:?}"),
        }
    }
}
impl StdError for WriteError {
    fn source(&self) -> Option<&(dyn StdError + 'static)> {
        match self {
            Self::Schema(source) => Some(source),
            Self::DefinitionEncoding(source) => Some(source),
            Self::PageImage(source) => Some(source),
            Self::MapEncoding(source) => Some(source),
            Self::ColumnProperties(source) => Some(source),
            Self::Resource(source) => Some(source),
            Self::Io(source) => Some(source),
            Self::Open(source) => Some(source),
            Self::Catalog(source) => Some(source),
            Self::Definition(source) => Some(source),
            Self::Rows(source) => Some(source),
            Self::Directory(source) => Some(source),
            Self::Encoding(source) => Some(source),
            Self::Value(source) => Some(source),
            Self::LongValue(source) => Some(source),
            Self::UsageMap(source) => Some(source),
            Self::Allocation(source) => Some(source),
            Self::Publish(source) => Some(source),
            Self::Index(source) => Some(source),
            Self::Compose(source) => Some(source),
            Self::CreatePublish(source) => Some(source),
            Self::NotFound(_)
            | Self::Unsupported(_)
            | Self::Mismatch(_)
            | Self::RelationshipConstraint { .. }
            | Self::ScalarRelationshipConstraint { .. }
            | Self::NullRelationshipConstraint { .. }
            | Self::ValidationRule { .. }
            | Self::UnsupportedSortOrder { .. } => None,
        }
    }
}

macro_rules! conversion {
    ($source:ty, $variant:ident) => {
        impl From<$source> for WriteError {
            fn from(source: $source) -> Self {
                Self::$variant(source)
            }
        }
    };
}
conversion!(crate::Error, Resource);
conversion!(crate::TableSchemaPlanError, Schema);
conversion!(crate::TableDefinitionWriteError, DefinitionEncoding);
conversion!(crate::PageImageError, PageImage);
conversion!(crate::UsageMapWriteError, MapEncoding);
conversion!(crate::ColumnPropertyError, ColumnProperties);
conversion!(std::io::Error, Io);
conversion!(crate::DatabaseOpenError, Open);
conversion!(crate::CatalogError, Catalog);
conversion!(crate::TableDefinitionError, Definition);
conversion!(crate::RowError, Rows);
conversion!(crate::RowDirectoryError, Directory);
conversion!(crate::RowWriteError, Encoding);
conversion!(crate::ValueError, Value);
conversion!(crate::LongValueError, LongValue);
conversion!(crate::PublishError, Publish);
conversion!(crate::IndexTreeError, Index);
