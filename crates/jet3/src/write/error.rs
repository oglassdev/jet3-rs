//! The shared failure of every write operation.
use crate::ColumnOrdinal;

/// A rejected request, malformed source, exhausted resource policy, or publication failure
/// of an insert, update, delete, schema edit or database creation.
#[derive(Debug, thiserror::Error)]
#[error("field update failed: {self:?}")]
#[non_exhaustive]
pub enum WriteError {
    /// A requested table schema is invalid.
    Schema(#[from] crate::TableSchemaPlanError),
    /// A table definition could not be encoded.
    DefinitionEncoding(#[from] crate::TableDefinitionWriteError),
    /// A schema edit could not encode a data page.
    PageImage(#[from] crate::PageImageError),
    /// A schema edit could not encode an allocation map.
    MapEncoding(#[from] crate::UsageMapWriteError),
    /// Named column properties or their storage are malformed.
    ColumnProperties(#[from] crate::ColumnPropertyError),
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
    Resource(#[from] crate::Error),
    /// File operation failed.
    Io(#[from] std::io::Error),
    /// Database header failure.
    Open(#[from] crate::DatabaseOpenError),
    /// Catalog failure.
    Catalog(#[from] crate::CatalogError),
    /// Table definition failure.
    Definition(#[from] crate::TableDefinitionError),
    /// Row traversal failure.
    Rows(#[from] crate::RowError),
    /// Row directory failure.
    Directory(#[from] crate::RowDirectoryError),
    /// Replacement value or fixed layout failed checked row encoding.
    Encoding(#[from] crate::RowWriteError),
    /// Existing indexed numeric value failed decoding.
    Value(#[from] crate::ValueError),
    /// Existing external long-value storage failed validation.
    LongValue(#[from] crate::LongValueError),
    /// Available map row is malformed.
    UsageMap(#[source] crate::UsageMapError),
    /// Allocation bitmap is malformed or exhausted its budget.
    Allocation(#[source] crate::AllocationMapError),
    /// Index metadata, tree, or row reference is malformed.
    Index(#[from] crate::IndexTreeError),
    /// Atomic publication failed; its stage indicates whether publication occurred.
    Publish(#[from] crate::PublishError),
    /// A new database could not be composed; nothing was written.
    #[error("database composition failed: {0}")]
    Compose(#[source] crate::ComposeError),
    /// A composed new database could not be written, checked, or published.
    #[error("database publication failed: {0}")]
    CreatePublish(#[source] crate::PublishError),
}

#[cfg(test)]
mod tests {
    use super::WriteError;

    #[test]
    fn creation_and_mutation_failures_keep_their_prefixes() {
        assert_eq!(
            WriteError::NotFound("row").to_string(),
            "field update failed: NotFound(\"row\")"
        );
        assert_eq!(
            WriteError::Compose(crate::ComposeError::UnsupportedMemoOption).to_string(),
            "database composition failed: Jet 3 bootstrap composition failed: UnsupportedMemoOption"
        );
    }
}
