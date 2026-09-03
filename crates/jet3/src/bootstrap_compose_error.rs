//! Structured failures of the bootstrap composer.

use super::*;

/// Structured failure while composing a fixed bootstrap image.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum BootstrapComposeError {
    Definition(TableDefinitionWriteError),
    UsageMap(UsageMapWriteError),
    Page(PageImageError),
    Row(RowWriteError),
    WholeFile(WholeFilePlanError),
    Encoding(Error),
    IndexPageFull {
        needed: usize,
        available: usize,
    },
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
}

impl fmt::Display for BootstrapComposeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "Jet 3 bootstrap composition failed: {self:?}")
    }
}

impl std::error::Error for BootstrapComposeError {
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
            Self::IndexPageFull { .. }
            | Self::UnobservedMapRowLayout
            | Self::UnobservedLongValueColumnCount { .. } => None,
        }
    }
}

impl From<TableDefinitionWriteError> for BootstrapComposeError {
    fn from(source: TableDefinitionWriteError) -> Self {
        Self::Definition(source)
    }
}
impl From<UsageMapWriteError> for BootstrapComposeError {
    fn from(source: UsageMapWriteError) -> Self {
        Self::UsageMap(source)
    }
}
impl From<PageImageError> for BootstrapComposeError {
    fn from(source: PageImageError) -> Self {
        Self::Page(source)
    }
}
impl From<RowWriteError> for BootstrapComposeError {
    fn from(source: RowWriteError) -> Self {
        Self::Row(source)
    }
}
impl From<WholeFilePlanError> for BootstrapComposeError {
    fn from(source: WholeFilePlanError) -> Self {
        Self::WholeFile(source)
    }
}
impl From<Error> for BootstrapComposeError {
    fn from(source: Error) -> Self {
        Self::Encoding(source)
    }
}
impl From<CatalogNameKeyError> for BootstrapComposeError {
    fn from(source: CatalogNameKeyError) -> Self {
        Self::NameKey(source)
    }
}

impl From<TableSchemaPlanError> for BootstrapComposeError {
    fn from(source: TableSchemaPlanError) -> Self {
        Self::Schema(source)
    }
}
