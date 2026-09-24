use std::fmt;

use crate::{
    CatalogError, Error, PageNumber, RowError, TableDefinitionError, ValueError, WriteError,
};

/// The first failed relationship catalog, metadata or key-inclusion check.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum RelationshipValidationError {
    /// Central catalog discovery or an endpoint catalog record is malformed.
    Catalog(CatalogError),
    /// A system or endpoint table definition is malformed.
    Definition(TableDefinitionError),
    /// A central or endpoint row stream is malformed.
    Rows(RowError),
    /// A central metadata field or endpoint key cannot be decoded.
    Value(ValueError),
    /// Input or cumulative resource limits prevent validation.
    Resource(Error),
    /// Reciprocal records, names or catalog inventories disagree.
    Metadata(&'static str),
    /// A non-null child key has no matching parent.
    Orphan {
        /// Definition root of the parent table.
        parent: PageNumber,
        /// Definition root of the child table.
        child: PageNumber,
        /// Missing parent key.
        value: i32,
    },
    /// A non-Long scalar or composite child key has no matching parent.
    ScalarOrphan {
        /// Definition root of the parent table.
        parent: PageNumber,
        /// Definition root of the child table.
        child: PageNumber,
    },
}

impl From<WriteError> for RelationshipValidationError {
    fn from(error: WriteError) -> Self {
        match error {
            WriteError::Catalog(source) => Self::Catalog(source),
            WriteError::Definition(source) => Self::Definition(source),
            WriteError::Rows(source) => Self::Rows(source),
            WriteError::Value(source) => Self::Value(source),
            WriteError::Resource(source) => Self::Resource(source),
            WriteError::Mismatch(detail)
            | WriteError::Unsupported(detail)
            | WriteError::NotFound(detail) => Self::Metadata(detail),
            WriteError::RelationshipConstraint {
                parent,
                child,
                value,
            } => Self::Orphan {
                parent,
                child,
                value,
            },
            WriteError::ScalarRelationshipConstraint { parent, child } => {
                Self::ScalarOrphan { parent, child }
            }
            _ => Self::Metadata("unexpected relationship reader failure"),
        }
    }
}

impl fmt::Display for RelationshipValidationError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{self:?}")
    }
}

impl std::error::Error for RelationshipValidationError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Catalog(source) => Some(source),
            Self::Definition(source) => Some(source),
            Self::Rows(source) => Some(source),
            Self::Value(source) => Some(source),
            Self::Resource(source) => Some(source),
            Self::Metadata(_) | Self::Orphan { .. } | Self::ScalarOrphan { .. } => None,
        }
    }
}
