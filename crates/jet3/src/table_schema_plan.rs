//! Crate-private typed planning of one new user table.
//!
//! This validates a caller-described table and assigns its appended pages. It
//! builds no page bytes and performs no I/O.
//!
//! `EXP-0087` observed, identically across three fresh replicas, that creating
//! a user table appends a definition root page numbered equal to the new
//! object's `MSysObjects` `Id`, then a page holding the table's usage-map rows,
//! then one index root when the table carries an index. It observed only tables
//! with zero or one index, so this module refuses more than one rather than
//! extrapolating an ordering nobody has observed.
//!
//! `EXP-0087` establishes no property grammar beyond the pinned framing, so
//! this module plans no `LvProp` payload. It also establishes no `Id`
//! allocation rule beyond the observed equality with the definition root page.

#![allow(
    dead_code,
    reason = "crate-private writer slice awaiting DAO validation"
)]

use std::fmt;

use crate::catalog_name_key::{CatalogNameKeyError, validate_catalog_name};
use crate::{ColumnPhysicalType, ColumnStorageKind, PageNumber};

/// Largest column count a planned table may carry.
const MAX_COLUMNS: usize = 255;
/// Largest field count one planned index may carry.
const MAX_INDEX_FIELDS: usize = 10;
/// Index count `EXP-0087` observed on a created table.
const MAX_OBSERVED_INDEXES: usize = 1;

/// One column of a table being planned.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct PlannedColumn<'a> {
    /// Column name in the database's configured code page.
    pub(crate) name: &'a [u8],
    /// Stored physical type.
    pub(crate) physical_type: ColumnPhysicalType,
    /// Whether the column occupies a fixed row slot or a variable one.
    pub(crate) storage: ColumnStorageKind,
    /// Declared size in bytes; zero for the long types that carry none.
    pub(crate) size: u16,
}

/// Whether a planned index is the table's primary one.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum PlannedIndexKind {
    /// The table's primary index, which `EXP-0087` observed is also unique.
    Primary,
    /// Any other index.
    Ordinary,
}

/// One index of a table being planned.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct PlannedIndex<'a> {
    /// Index name in the database's configured code page.
    pub(crate) name: &'a [u8],
    /// Zero-based ordinals of the indexed columns, in key order.
    pub(crate) fields: &'a [u16],
    /// Whether this is the table's primary index.
    pub(crate) kind: PlannedIndexKind,
}

/// A caller-described user table awaiting validation and page assignment.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct TableSchemaSpec<'a> {
    /// Table name in the database's configured code page.
    pub(crate) name: &'a [u8],
    /// The table's columns in ordinal order.
    pub(crate) columns: &'a [PlannedColumn<'a>],
    /// The table's indexes.
    pub(crate) indexes: &'a [PlannedIndex<'a>],
}

/// Structured failure while planning one new user table.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum TableSchemaPlanError {
    /// The table name cannot be encoded into a catalog index key.
    TableName(CatalogNameKeyError),
    /// A column declares an empty name.
    EmptyColumnName {
        /// Zero-based ordinal of the rejected column.
        ordinal: usize,
    },
    /// An index declares an empty name.
    EmptyIndexName {
        /// Zero-based position of the rejected index.
        index: usize,
    },
    /// The table declares no columns.
    NoColumns,
    /// The table declares more columns than a planned table may carry.
    TooManyColumns {
        /// Declared column count.
        count: usize,
        /// Largest supported column count.
        limit: usize,
    },
    /// Two columns share a name.
    DuplicateColumnName {
        /// Ordinal of the earlier column.
        first: usize,
        /// Ordinal of the later column.
        second: usize,
    },
    /// Two indexes share a name.
    DuplicateIndexName {
        /// Position of the earlier index.
        first: usize,
        /// Position of the later index.
        second: usize,
    },
    /// `EXP-0087` observed no create carrying this many indexes.
    UnobservedIndexCount {
        /// Declared index count.
        count: usize,
        /// Largest observed index count.
        observed: usize,
    },
    /// An index names no columns.
    IndexWithoutFields {
        /// Position of the rejected index.
        index: usize,
    },
    /// An index names more columns than one index may carry.
    TooManyIndexFields {
        /// Position of the rejected index.
        index: usize,
        /// Declared field count.
        count: usize,
        /// Largest supported field count.
        limit: usize,
    },
    /// An index names a column the table does not declare.
    IndexFieldOutOfRange {
        /// Position of the rejected index.
        index: usize,
        /// The out-of-range column ordinal.
        column: u16,
    },
    /// An index names the same column twice.
    DuplicateIndexField {
        /// Position of the rejected index.
        index: usize,
        /// The repeated column ordinal.
        column: u16,
    },
    /// The table declares more than one primary index.
    MultiplePrimaryIndexes {
        /// Position of the earlier primary index.
        first: usize,
        /// Position of the later primary index.
        second: usize,
    },
    /// The appended pages do not fit the addressable page space.
    PageOverflow {
        /// Page the appended run would have started at.
        first: u64,
        /// Pages the run needs.
        needed: u64,
    },
}

impl fmt::Display for TableSchemaPlanError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "Jet 3 table schema planning failed: {self:?}")
    }
}

impl std::error::Error for TableSchemaPlanError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::TableName(source) => Some(source),
            _ => None,
        }
    }
}

/// The validated page assignment for one new user table.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct TableSchemaPlan {
    object_id: i32,
    definition_root: PageNumber,
    map_page: PageNumber,
    index_root: Option<PageNumber>,
}

impl TableSchemaPlan {
    /// Returns the `MSysObjects` `Id` the new table takes.
    pub(crate) const fn object_id(&self) -> i32 {
        self.object_id
    }

    /// Returns the page holding the table's definition.
    pub(crate) const fn definition_root(&self) -> PageNumber {
        self.definition_root
    }

    /// Returns the page holding the table's usage-map rows.
    pub(crate) const fn map_page(&self) -> PageNumber {
        self.map_page
    }

    /// Returns the table's index root page, if it carries an index.
    pub(crate) const fn index_root(&self) -> Option<PageNumber> {
        self.index_root
    }

    /// Returns how many pages the create appends.
    pub(crate) const fn appended_page_count(&self) -> u64 {
        if self.index_root.is_some() { 3 } else { 2 }
    }
}

/// Validates `spec` and assigns its appended pages starting at `first_page`.
///
/// `first_page` is the database's current page count, so the appended run is
/// contiguous from it. The `EXP-0087` assignment is the definition root, then
/// the map-rows page, then the index root when the table carries an index.
pub(crate) fn plan_table_schema(
    spec: &TableSchemaSpec<'_>,
    first_page: u64,
) -> Result<TableSchemaPlan, TableSchemaPlanError> {
    validate_names(spec)?;
    validate_indexes(spec)?;
    let needed = if spec.indexes.is_empty() { 2 } else { 3 };
    let object_id = i32::try_from(first_page)
        .ok()
        .filter(|_| first_page.checked_add(needed).is_some())
        .ok_or(TableSchemaPlanError::PageOverflow {
            first: first_page,
            needed,
        })?;
    Ok(TableSchemaPlan {
        object_id,
        definition_root: PageNumber::new(first_page),
        map_page: PageNumber::new(first_page + 1),
        index_root: (needed == 3).then(|| PageNumber::new(first_page + 2)),
    })
}

fn validate_names(spec: &TableSchemaSpec<'_>) -> Result<(), TableSchemaPlanError> {
    validate_catalog_name(spec.name).map_err(TableSchemaPlanError::TableName)?;
    if spec.columns.is_empty() {
        return Err(TableSchemaPlanError::NoColumns);
    }
    if spec.columns.len() > MAX_COLUMNS {
        return Err(TableSchemaPlanError::TooManyColumns {
            count: spec.columns.len(),
            limit: MAX_COLUMNS,
        });
    }
    for (ordinal, column) in spec.columns.iter().enumerate() {
        if column.name.is_empty() {
            return Err(TableSchemaPlanError::EmptyColumnName { ordinal });
        }
        if let Some(first) = spec.columns[..ordinal]
            .iter()
            .position(|earlier| earlier.name == column.name)
        {
            return Err(TableSchemaPlanError::DuplicateColumnName {
                first,
                second: ordinal,
            });
        }
    }
    for (index, planned) in spec.indexes.iter().enumerate() {
        if planned.name.is_empty() {
            return Err(TableSchemaPlanError::EmptyIndexName { index });
        }
        if let Some(first) = spec.indexes[..index]
            .iter()
            .position(|earlier| earlier.name == planned.name)
        {
            return Err(TableSchemaPlanError::DuplicateIndexName {
                first,
                second: index,
            });
        }
    }
    Ok(())
}

fn validate_indexes(spec: &TableSchemaSpec<'_>) -> Result<(), TableSchemaPlanError> {
    if spec.indexes.len() > MAX_OBSERVED_INDEXES {
        return Err(TableSchemaPlanError::UnobservedIndexCount {
            count: spec.indexes.len(),
            observed: MAX_OBSERVED_INDEXES,
        });
    }
    let mut primary: Option<usize> = None;
    for (index, planned) in spec.indexes.iter().enumerate() {
        if planned.fields.is_empty() {
            return Err(TableSchemaPlanError::IndexWithoutFields { index });
        }
        if planned.fields.len() > MAX_INDEX_FIELDS {
            return Err(TableSchemaPlanError::TooManyIndexFields {
                index,
                count: planned.fields.len(),
                limit: MAX_INDEX_FIELDS,
            });
        }
        for (position, &column) in planned.fields.iter().enumerate() {
            if usize::from(column) >= spec.columns.len() {
                return Err(TableSchemaPlanError::IndexFieldOutOfRange { index, column });
            }
            if planned.fields[..position].contains(&column) {
                return Err(TableSchemaPlanError::DuplicateIndexField { index, column });
            }
        }
        if planned.kind == PlannedIndexKind::Primary
            && let Some(first) = primary.replace(index)
        {
            return Err(TableSchemaPlanError::MultiplePrimaryIndexes {
                first,
                second: index,
            });
        }
    }
    Ok(())
}

#[cfg(test)]
#[path = "table_schema_plan_tests.rs"]
mod tests;
