//! Structured failures of the bootstrap composer.

use super::*;

/// Structured failure while composing a database image.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ComposeError {
    /// Memo empty-string option is outside the supported first-table schema.
    UnsupportedMemoOption,
    /// A non-null child key has no matching initial parent row.
    OrphanInitialRelationshipKey {
        /// Zero-based child input row.
        row: usize,
        /// Unmatched Long key.
        value: i32,
    },
    /// A non-Long scalar or composite child key has no matching initial parent row.
    OrphanInitialScalarRelationshipKey {
        /// Zero-based child input row.
        row: usize,
    },
    /// The relationship request exceeds the supported schema or references.
    UnsupportedRelationship {
        /// Unsupported relationship constraint.
        detail: &'static str,
    },
    /// Initial indexes require one or two supported numeric columns and bounded index counts.
    UnsupportedInitialIndexSchema,
    /// A scalar key value needs an unsupported encoding (including negative zero).
    UnsupportedInitialIndexValue {
        /// Zero-based input row.
        row: usize,
        /// Zero-based table column.
        column: usize,
    },
    /// A unique index repeats a non-null scalar key other than an all-Long key.
    DuplicateInitialScalarIndexKey,
    /// A required index or relationship key contains a null component.
    NullInitialIndexKey {
        /// Zero-based input row.
        row: usize,
    },
    /// A primary or unique initial index repeats a two-Long key.
    DuplicateInitialCompositeIndexKey {
        /// Repeated values in declared index-field order.
        values: [i32; 2],
    },
    /// A primary or unique initial index repeats a key.
    DuplicateInitialIndexKey {
        /// Repeated Long value.
        value: i32,
    },
    /// An initial long value is outside the bounded payload construction.
    InitialLongValue {
        /// Zero-based input row.
        row: usize,
        /// The unsupported payload condition.
        detail: &'static str,
    },
    /// Initial generated IDs exceed the supported construction.
    InitialAutoIncrement {
        /// Unsupported generation request or counter boundary.
        detail: &'static str,
    },
    /// Initial rows require a single-page definition.
    UnsupportedInitialRowSchema,
    /// A table definition could not be encoded.
    Definition(TableDefinitionWriteError),
    /// A usage map could not be encoded.
    UsageMap(UsageMapWriteError),
    /// A page image could not be written.
    Page(PageImageError),
    /// A data row could not be encoded.
    Row(RowWriteError),
    /// The whole-file page plan could not be extended.
    WholeFile(WholeFilePlanError),
    /// A low-level encoding or resource limit failed.
    Encoding(Error),
    /// Catalog growth would exceed the inline allocation maps.
    CatalogPageLimit {
        /// Exclusive page-number bound.
        maximum: u64,
    },
    /// A catalog composition invariant did not hold.
    CatalogLayout {
        /// Failed checked layout condition.
        detail: &'static str,
    },
    /// A bootstrap index page cannot hold its entries.
    IndexPageFull {
        /// Bytes the entries need.
        needed: usize,
        /// Bytes the entry area holds.
        available: usize,
    },
    /// A catalog name could not be encoded into an index key.
    NameKey(CatalogNameKeyError),
    /// The table could not be planned.
    Schema(TableSchemaPlanError),
    /// The table carries both an index and a long-value column, a map-page
    /// row layout `EXP-0087` never observed.
    UnobservedMapRowLayout,
    /// `EXP-0087` observed no create carrying this many long-value columns.
    UnobservedLongValueColumnCount {
        /// Largest observed long-value column count.
        observed: usize,
    },
    /// The table count would overflow the bounded page-zero creation counter.
    TableCountOverflow {
        /// Requested table count.
        count: usize,
        /// Largest count representable without counter overflow.
        maximum: usize,
    },
    /// Two tables share a name under the provider's case-folding comparison.
    DuplicateTableName {
        /// Position of the earlier table.
        first: usize,
        /// Position of the later table.
        second: usize,
    },
    /// The encoded definition length differs from the length the planner
    /// measured, so its page split cannot be trusted.
    DefinitionLengthMismatch {
        /// Length the planner measured.
        planned: usize,
        /// Length the encoder produced.
        encoded: usize,
    },
}

impl fmt::Display for ComposeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "Jet 3 bootstrap composition failed: {self:?}")
    }
}

impl std::error::Error for ComposeError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Definition(source) => Some(source),
            Self::UsageMap(source) => Some(source),
            Self::Page(source) => Some(source),
            Self::Row(source) => Some(source),
            Self::WholeFile(source) => Some(source),
            Self::Encoding(source) => Some(source),
            Self::NameKey(source) => Some(source),
            Self::Schema(source) => Some(source),
            Self::UnsupportedRelationship { .. }
            | Self::UnsupportedInitialRowSchema
            | Self::InitialLongValue { .. }
            | Self::UnsupportedMemoOption
            | Self::InitialAutoIncrement { .. }
            | Self::OrphanInitialRelationshipKey { .. }
            | Self::OrphanInitialScalarRelationshipKey { .. }
            | Self::UnsupportedInitialIndexSchema
            | Self::UnsupportedInitialIndexValue { .. }
            | Self::DuplicateInitialScalarIndexKey
            | Self::NullInitialIndexKey { .. }
            | Self::DuplicateInitialIndexKey { .. }
            | Self::DuplicateInitialCompositeIndexKey { .. }
            | Self::IndexPageFull { .. }
            | Self::CatalogPageLimit { .. }
            | Self::CatalogLayout { .. }
            | Self::UnobservedMapRowLayout
            | Self::UnobservedLongValueColumnCount { .. }
            | Self::TableCountOverflow { .. }
            | Self::DuplicateTableName { .. }
            | Self::DefinitionLengthMismatch { .. } => None,
        }
    }
}

impl From<TableDefinitionWriteError> for ComposeError {
    fn from(source: TableDefinitionWriteError) -> Self {
        Self::Definition(source)
    }
}
impl From<UsageMapWriteError> for ComposeError {
    fn from(source: UsageMapWriteError) -> Self {
        Self::UsageMap(source)
    }
}
impl From<PageImageError> for ComposeError {
    fn from(source: PageImageError) -> Self {
        Self::Page(source)
    }
}
impl From<RowWriteError> for ComposeError {
    fn from(source: RowWriteError) -> Self {
        Self::Row(source)
    }
}
impl From<WholeFilePlanError> for ComposeError {
    fn from(source: WholeFilePlanError) -> Self {
        Self::WholeFile(source)
    }
}
impl From<Error> for ComposeError {
    fn from(source: Error) -> Self {
        Self::Encoding(source)
    }
}
impl From<CatalogNameKeyError> for ComposeError {
    fn from(source: CatalogNameKeyError) -> Self {
        Self::NameKey(source)
    }
}

impl From<TableSchemaPlanError> for ComposeError {
    fn from(source: TableSchemaPlanError) -> Self {
        Self::Schema(source)
    }
}
