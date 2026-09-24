use crate::{
    CatalogError, Error, PageNumber, RowError, TableDefinitionError, ValueError, WriteError,
};

/// The first failed relationship catalog, metadata or key-inclusion check.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[non_exhaustive]
#[error("{self:?}")]
pub enum RelationshipValidationError {
    /// Central catalog discovery or an endpoint catalog record is malformed.
    Catalog(#[source] CatalogError),
    /// A system or endpoint table definition is malformed.
    Definition(#[source] TableDefinitionError),
    /// A central or endpoint row stream is malformed.
    Rows(#[source] RowError),
    /// A central metadata field or endpoint key cannot be decoded.
    Value(#[source] ValueError),
    /// Input or cumulative resource limits prevent validation.
    Resource(#[source] Error),
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
