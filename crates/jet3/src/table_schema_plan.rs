//! Crate-private typed planning of one new user table.
//!
//! This validates a caller-described table and assigns its appended pages. It
//! builds no page bytes and performs no I/O.
//!
//! Column classes, sizes, row layout, key columns, and name lengths are
//! validated by the same table-definition and catalog encoders that will later
//! write the table, so a plan this module accepts is one those encoders accept.
//!
//! `EXP-0087` observed, identically across three fresh replicas, that creating
//! a user table appends a definition root page numbered equal to the new
//! object's `MSysObjects` `Id`, then a page holding the table's usage-map rows,
//! then one index root when the table carries an index. It observed only tables
//! with zero or one index, so this module refuses more than one rather than
//! extrapolating an ordering nobody has observed.
//!
//! `EXP-0087` establishes no property grammar beyond the pinned framing, so
//! this module plans no `LvProp` payload and no long-value page. `EXP-0087`
//! observed that a database's first create also appends a long-value page, so
//! a caller composing that first create must account for it separately. It
//! also establishes no `Id` allocation rule beyond the observed equality with
//! the definition root page.

#![allow(
    dead_code,
    reason = "crate-private writer slice awaiting DAO validation"
)]

use std::fmt;

use crate::catalog_name_key::{CatalogNameKeyError, validate_catalog_name};
use crate::catalog_record_writer::{CatalogRecordWriteError, catalog_record_len};
use crate::column_definition_writer::{PhysicalIndexSpec, validate_physical_index};
use crate::page_image::PAGE_BYTES;
use crate::table_definition_layout::{definition_len, validate_column_layout, validate_name};
use crate::{
    ColumnPhysicalType, ColumnSpec, IndexFieldSpec, PageNumber, PhysicalIndexFlagsSpec,
    TableDefinitionKind, TableDefinitionWriteError,
};

/// Index count `EXP-0087` observed on a created table.
const MAX_OBSERVED_INDEXES: usize = 1;
/// `EXP-0057`: usage-map locators hold a three-byte page number.
const MAX_MAP_PAGE: u64 = 0x00ff_ffff;
/// Row slot the planned table's own usage map takes on its map page.
const OWNED_MAP_ROW: u8 = 0;
/// Bytes of a definition the root page holds; longer needs a continuation.
const DEFINITION_ROOT_CAPACITY: usize = PAGE_BYTES;

/// Whether a planned index is the table's primary one.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum PlannedIndexKind {
    /// The table's primary index, which `EXP-0087` observed is also unique.
    Primary,
    /// Any other index.
    Ordinary,
}

impl PlannedIndexKind {
    /// Returns the physical flag class this index kind encodes to.
    const fn flags(self) -> PhysicalIndexFlagsSpec {
        match self {
            Self::Primary => PhysicalIndexFlagsSpec::UniqueRequired,
            Self::Ordinary => PhysicalIndexFlagsSpec::Ordinary,
        }
    }
}

/// One index of a table being planned.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct PlannedIndex<'a> {
    /// Index name in the database's configured code page.
    pub(crate) name: &'a [u8],
    /// Ordered key fields.
    pub(crate) fields: &'a [IndexFieldSpec],
    /// Whether this is the table's primary index.
    pub(crate) kind: PlannedIndexKind,
}

/// A caller-described user table awaiting validation and page assignment.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct TableSchemaSpec<'a> {
    /// Table name in the database's configured code page.
    pub(crate) name: &'a [u8],
    /// The table's columns in ordinal order.
    pub(crate) columns: &'a [ColumnSpec<'a>],
    /// The table's indexes.
    pub(crate) indexes: &'a [PlannedIndex<'a>],
}

/// Structured failure while planning one new user table.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum TableSchemaPlanError {
    /// The table name cannot be encoded into a catalog index key.
    TableNameKey(CatalogNameKeyError),
    /// The table name cannot be encoded into an `MSysObjects` row.
    TableNameRow(CatalogRecordWriteError),
    /// The columns or indexes cannot be encoded into a table definition.
    Definition(TableDefinitionWriteError),
    /// The table declares no columns.
    NoColumns,
    /// The definition needs a continuation page, whose placement no experiment
    /// has established.
    DefinitionNeedsContinuation {
        /// Encoded definition length.
        length: usize,
        /// Bytes the definition root page holds.
        capacity: usize,
    },
    /// `EXP-0087` observed no create carrying this many indexes.
    UnobservedIndexCount {
        /// Declared index count.
        count: usize,
        /// Largest observed index count.
        observed: usize,
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
    /// The map page cannot be named by a three-byte usage-map locator.
    MapPageNotAddressable {
        /// The unaddressable map page.
        page: u64,
        /// Highest page a locator can name.
        maximum: u64,
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
            Self::TableNameKey(source) => Some(source),
            Self::TableNameRow(source) => Some(source),
            Self::Definition(source) => Some(source),
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

    /// Returns how many pages the create appends, excluding the long-value
    /// page `EXP-0087` observed only on a database's first create.
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
    validate_table_name(spec.name)?;
    if spec.columns.is_empty() {
        return Err(TableSchemaPlanError::NoColumns);
    }
    validate_column_layout(spec.columns, TableDefinitionKind::User, &[])
        .map_err(TableSchemaPlanError::Definition)?;
    validate_definition_fits_root(spec)?;
    let plan = assign_pages(spec, first_page)?;
    validate_indexes(spec, &plan)?;
    Ok(plan)
}

/// Checks the table name against both encodings that will carry it.
fn validate_table_name(name: &[u8]) -> Result<(), TableSchemaPlanError> {
    validate_catalog_name(name).map_err(TableSchemaPlanError::TableNameKey)?;
    catalog_record_len(name.len()).map_err(TableSchemaPlanError::TableNameRow)?;
    Ok(())
}

/// Refuses a definition too long for its root page.
///
/// `EXP-0087` observed every create appending a definition root, a map-rows
/// page, and at most an index root, so no observed definition needed a
/// continuation page and nothing establishes where one would land.
fn validate_definition_fits_root(spec: &TableSchemaSpec<'_>) -> Result<(), TableSchemaPlanError> {
    // The writer requires one long-value map group per Memo or LongBinary
    // column, so the group count follows from the columns.
    let long_value_maps = spec
        .columns
        .iter()
        .filter(|column| {
            matches!(
                column.physical_type(),
                ColumnPhysicalType::Memo | ColumnPhysicalType::LongBinary
            )
        })
        .count();
    let length = definition_len(
        spec.columns,
        spec.indexes.iter().map(|index| index.name),
        spec.indexes.len(),
        long_value_maps,
    )
    .map_err(TableSchemaPlanError::Definition)?;
    if length > DEFINITION_ROOT_CAPACITY {
        return Err(TableSchemaPlanError::DefinitionNeedsContinuation {
            length,
            capacity: DEFINITION_ROOT_CAPACITY,
        });
    }
    Ok(())
}

/// Assigns the appended page run, refusing numbers the encoders cannot name.
fn assign_pages(
    spec: &TableSchemaSpec<'_>,
    first_page: u64,
) -> Result<TableSchemaPlan, TableSchemaPlanError> {
    let needed = if spec.indexes.is_empty() { 2 } else { 3 };
    // `EXP-0087` numbers the object equal to its definition root, and
    // `MSysObjects.Id` is a signed Long, so the run must stay in that range.
    let object_id = i32::try_from(first_page)
        .ok()
        .filter(|_| first_page.checked_add(needed).is_some())
        .ok_or(TableSchemaPlanError::PageOverflow {
            first: first_page,
            needed,
        })?;
    let map_page = first_page + 1;
    if map_page > MAX_MAP_PAGE {
        return Err(TableSchemaPlanError::MapPageNotAddressable {
            page: map_page,
            maximum: MAX_MAP_PAGE,
        });
    }
    Ok(TableSchemaPlan {
        object_id,
        definition_root: PageNumber::new(first_page),
        map_page: PageNumber::new(map_page),
        index_root: (needed == 3).then(|| PageNumber::new(first_page + 2)),
    })
}

/// Checks each index against the physical-index encoder and the observed count.
fn validate_indexes(
    spec: &TableSchemaSpec<'_>,
    plan: &TableSchemaPlan,
) -> Result<(), TableSchemaPlanError> {
    if spec.indexes.len() > MAX_OBSERVED_INDEXES {
        return Err(TableSchemaPlanError::UnobservedIndexCount {
            count: spec.indexes.len(),
            observed: MAX_OBSERVED_INDEXES,
        });
    }
    let mut primary: Option<usize> = None;
    for (position, planned) in spec.indexes.iter().enumerate() {
        let ordinal = position as u16;
        validate_name(
            "logical index",
            ordinal,
            planned.name,
            spec.indexes[..position].iter().map(|earlier| earlier.name),
        )
        .map_err(TableSchemaPlanError::Definition)?;
        let root = plan
            .index_root
            .ok_or(TableSchemaPlanError::UnobservedIndexCount {
                count: spec.indexes.len(),
                observed: MAX_OBSERVED_INDEXES,
            })?;
        let physical = PhysicalIndexSpec {
            fields: planned.fields,
            usage_map_page: plan.map_page,
            usage_map_row: OWNED_MAP_ROW,
            root,
            flags: planned.kind.flags(),
            entry_count: 0,
        };
        validate_physical_index(ordinal, &physical, spec.columns)
            .map_err(TableSchemaPlanError::Definition)?;
        if planned.kind == PlannedIndexKind::Primary
            && let Some(first) = primary.replace(position)
        {
            return Err(TableSchemaPlanError::MultiplePrimaryIndexes {
                first,
                second: position,
            });
        }
    }
    Ok(())
}

#[cfg(test)]
#[path = "table_schema_plan_tests.rs"]
mod tests;
