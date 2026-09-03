//! Crate-private typed planning of one new user table as a database's first
//! create.
//!
//! This validates a caller-described table and assigns its appended pages. It
//! builds no page bytes and performs no I/O.
//!
//! Column classes, sizes, row layout, key columns, and name lengths are
//! validated by the same table-definition and catalog encoders that will later
//! write the table, so a plan this module accepts is one those encoders accept.
//!
//! `EXP-0093` observed, identically across three replicas and four arms, that
//! a first create appends a definition root page numbered equal to the new
//! object's `MSysObjects` `Id`, then the page holding the table's usage-map
//! rows, then the page holding the catalog row's `LvProp` long value, then one
//! empty index root per physical index in append order. Each index's map row
//! is `2 + physical_ordinal` on the map page. It observed at most three
//! indexes, so this module refuses more rather than extrapolating.
//!
//! `EXP-0105` observed that a definition longer than its root page continues
//! on one or two further definition pages, the root holding 2,048 logical
//! bytes and each continuation 2,040. It observed the two-continuation chain
//! pointing to the physically later page first. It observed no create carrying
//! both an index and a continuation, so that combined page order is refused.
//!
//! Neither experiment establishes an `Id` allocation rule beyond the observed
//! equality with the definition root page.

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
    ColumnPhysicalType, ColumnSpec, IndexFieldSpec, LogicalIndexKindSpec, PageNumber,
    PhysicalIndexFlagsSpec, TableDefinitionKind, TableDefinitionWriteError,
};

/// Largest index count `EXP-0093` observed on a created table.
pub(crate) const MAX_OBSERVED_INDEXES: usize = 3;
/// Largest continuation count `EXP-0105` observed on a created table.
pub(crate) const MAX_OBSERVED_CONTINUATIONS: usize = 2;
/// `EXP-0057`: usage-map locators hold a three-byte page number.
const MAX_MAP_PAGE: u64 = 0x00ff_ffff;
/// `EXP-0093`: map-page row of the table's owned-page map.
pub(crate) const OWNED_MAP_ROW: u8 = 0;
/// `EXP-0093`: map-page row of the table's available-page map.
pub(crate) const AVAILABLE_MAP_ROW: u8 = 1;
/// `EXP-0093`: map-page row of the first index's map; later indexes follow.
pub(crate) const FIRST_INDEX_MAP_ROW: u8 = 2;
/// `EXP-0059`, `EXP-0105`: logical definition bytes the root page holds.
pub(crate) const DEFINITION_ROOT_CAPACITY: usize = PAGE_BYTES;
/// `EXP-0059`, `EXP-0105`: logical definition bytes one continuation holds,
/// after its four-byte prefix and four-byte next-page reference.
pub(crate) const CONTINUATION_CAPACITY: usize = PAGE_BYTES - CONTINUATION_PAYLOAD_OFFSET;
/// `EXP-0059`: continuation payload starts after the prefix and next pointer.
pub(crate) const CONTINUATION_PAYLOAD_OFFSET: usize = 8;
/// `EXP-0087`: highest name byte with an established catalog-key weight.
/// `EXP-0101` is bounded and does not widen this.
const LAST_ESTABLISHED_NAME_BYTE: u8 = 0x7e;

/// The uniqueness class of a planned index.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum PlannedIndexKind {
    /// The table's primary index; `EXP-0093` observed physical flags `0x09`.
    Primary,
    /// A unique non-primary index; `EXP-0093` observed physical flags `0x01`.
    Unique,
    /// Any other index; `EXP-0093` observed physical flags `0x00`.
    Ordinary,
}

impl PlannedIndexKind {
    /// Returns the physical flag class this index kind encodes to.
    pub(crate) const fn flags(self) -> PhysicalIndexFlagsSpec {
        match self {
            Self::Primary => PhysicalIndexFlagsSpec::UniqueRequired,
            Self::Unique => PhysicalIndexFlagsSpec::Unique,
            Self::Ordinary => PhysicalIndexFlagsSpec::Ordinary,
        }
    }

    /// Returns the logical record class this index kind encodes to.
    pub(crate) const fn logical_kind(self) -> LogicalIndexKindSpec {
        match self {
            Self::Primary => LogicalIndexKindSpec::Primary,
            Self::Unique | Self::Ordinary => LogicalIndexKindSpec::Ordinary,
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
    /// The index's uniqueness class.
    pub(crate) kind: PlannedIndexKind,
}

/// A caller-described user table awaiting validation and page assignment.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct TableSchemaSpec<'a> {
    /// Table name in the database's configured code page.
    pub(crate) name: &'a [u8],
    /// The table's columns in ordinal order.
    pub(crate) columns: &'a [ColumnSpec<'a>],
    /// The table's indexes in physical (append) order.
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
    /// A column or index name holds a byte above the range `EXP-0087`
    /// established weights for.
    NameByteUnestablished {
        /// `"column"` or `"logical index"`.
        role: &'static str,
        /// Position of the column or index in the spec.
        ordinal: usize,
        /// Position of the byte in the name.
        position: usize,
        /// The unestablished byte.
        byte: u8,
    },
    /// The definition needs more continuation pages than `EXP-0105`
    /// observed.
    UnobservedContinuationCount {
        /// Encoded definition length.
        length: usize,
        /// Continuations the definition needs.
        count: usize,
        /// Largest observed continuation count.
        observed: usize,
    },
    /// `EXP-0093` observed no create carrying this many indexes.
    UnobservedIndexCount {
        /// Declared index count.
        count: usize,
        /// Largest observed index count.
        observed: usize,
    },
    /// The table carries both indexes and a definition continuation, a page
    /// order no experiment has observed together.
    UnobservedIndexContinuationLayout {
        /// Declared index count.
        indexes: usize,
        /// Continuations the definition needs.
        continuations: usize,
    },
    /// The table declares more than one primary index.
    MultiplePrimaryIndexes {
        /// Position of the earlier primary index.
        first: usize,
        /// Position of the later primary index.
        second: usize,
    },
    /// Two index names order differently by byte value and by ASCII
    /// case-folded value, so their observed name order is underdetermined.
    UnderdeterminedIndexNameOrder {
        /// Position of one index.
        first: usize,
        /// Position of the other index.
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
///
/// The appended run is the definition root, the map page, the `LvProp` page,
/// then either the index roots or the continuation pages; a plan never carries
/// both.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct TableSchemaPlan {
    object_id: i32,
    definition_root: PageNumber,
    definition_len: usize,
    index_count: usize,
    continuation_count: usize,
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

    /// Returns the exact logical length of the encoded definition.
    pub(crate) const fn definition_len(&self) -> usize {
        self.definition_len
    }

    /// Returns the page holding the table's usage-map rows.
    pub(crate) const fn map_page(&self) -> PageNumber {
        PageNumber::new(self.definition_root.get() + 1)
    }

    /// Returns the page holding the catalog row's `LvProp` long value.
    pub(crate) const fn property_page(&self) -> PageNumber {
        PageNumber::new(self.definition_root.get() + 2)
    }

    /// Returns each index's root page and map-page row in physical ordinal
    /// order (`EXP-0093`: roots follow the `LvProp` page, rows follow the
    /// table's own two maps).
    pub(crate) fn index_placements(&self) -> impl Iterator<Item = (PageNumber, u8)> {
        let first_root = self.property_page().get() + 1;
        (0..self.index_count).map(move |ordinal| {
            (
                PageNumber::new(first_root + ordinal as u64),
                FIRST_INDEX_MAP_ROW + ordinal as u8,
            )
        })
    }

    /// Returns the definition continuation pages in chain order.
    ///
    /// The pages are appended in ascending physical order. `EXP-0105`
    /// observed the two-continuation chain visiting the physically later page
    /// first, so the two-page chain is reversed relative to file order.
    pub(crate) fn continuation_chain(&self) -> impl Iterator<Item = PageNumber> {
        let first = self.property_page().get() + 1;
        let pages = match self.continuation_count {
            0 => [None, None],
            1 => [Some(first), None],
            _ => [Some(first + 1), Some(first)],
        };
        pages.into_iter().flatten().map(PageNumber::new)
    }

    /// Returns how many pages the create appends.
    pub(crate) const fn appended_page_count(&self) -> u64 {
        3 + self.index_count as u64 + self.continuation_count as u64
    }
}

/// Validates `spec` as a first create and assigns its appended pages starting
/// at `first_page`, the database's current page count.
pub(crate) fn plan_table_schema(
    spec: &TableSchemaSpec<'_>,
    first_page: u64,
) -> Result<TableSchemaPlan, TableSchemaPlanError> {
    validate_table_name(spec.name)?;
    if spec.columns.is_empty() {
        return Err(TableSchemaPlanError::NoColumns);
    }
    for (ordinal, column) in spec.columns.iter().enumerate() {
        validate_name_bytes("column", ordinal, column.name())?;
    }
    validate_column_layout(spec.columns, TableDefinitionKind::User, &[])
        .map_err(TableSchemaPlanError::Definition)?;
    if spec.indexes.len() > MAX_OBSERVED_INDEXES {
        return Err(TableSchemaPlanError::UnobservedIndexCount {
            count: spec.indexes.len(),
            observed: MAX_OBSERVED_INDEXES,
        });
    }
    let definition_len = measure_definition(spec)?;
    let continuation_count = continuation_count(definition_len);
    if continuation_count > MAX_OBSERVED_CONTINUATIONS {
        return Err(TableSchemaPlanError::UnobservedContinuationCount {
            length: definition_len,
            count: continuation_count,
            observed: MAX_OBSERVED_CONTINUATIONS,
        });
    }
    if !spec.indexes.is_empty() && continuation_count > 0 {
        return Err(TableSchemaPlanError::UnobservedIndexContinuationLayout {
            indexes: spec.indexes.len(),
            continuations: continuation_count,
        });
    }
    let plan = assign_pages(spec, first_page, definition_len, continuation_count)?;
    validate_indexes(spec, &plan)?;
    Ok(plan)
}

/// Checks the table name against both encodings that will carry it.
fn validate_table_name(name: &[u8]) -> Result<(), TableSchemaPlanError> {
    validate_catalog_name(name).map_err(TableSchemaPlanError::TableNameKey)?;
    catalog_record_len(name.len()).map_err(TableSchemaPlanError::TableNameRow)?;
    Ok(())
}

/// Refuses name bytes outside the range `EXP-0087` established.
fn validate_name_bytes(
    role: &'static str,
    ordinal: usize,
    name: &[u8],
) -> Result<(), TableSchemaPlanError> {
    match name
        .iter()
        .position(|byte| *byte > LAST_ESTABLISHED_NAME_BYTE)
    {
        Some(position) => Err(TableSchemaPlanError::NameByteUnestablished {
            role,
            ordinal,
            position,
            byte: name[position],
        }),
        None => Ok(()),
    }
}

/// Returns the exact logical length of the definition `spec` encodes to.
fn measure_definition(spec: &TableSchemaSpec<'_>) -> Result<usize, TableSchemaPlanError> {
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
    definition_len(
        spec.columns,
        spec.indexes.iter().map(|index| index.name),
        spec.indexes.len(),
        long_value_maps,
    )
    .map_err(TableSchemaPlanError::Definition)
}

/// Returns how many continuation pages a definition of `length` bytes needs.
pub(crate) const fn continuation_count(length: usize) -> usize {
    length
        .saturating_sub(DEFINITION_ROOT_CAPACITY)
        .div_ceil(CONTINUATION_CAPACITY)
}

/// Assigns the appended page run, refusing numbers the encoders cannot name.
fn assign_pages(
    spec: &TableSchemaSpec<'_>,
    first_page: u64,
    definition_len: usize,
    continuation_count: usize,
) -> Result<TableSchemaPlan, TableSchemaPlanError> {
    let needed = 3 + spec.indexes.len() as u64 + continuation_count as u64;
    // `EXP-0093` numbers the object equal to its definition root, and
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
        definition_len,
        index_count: spec.indexes.len(),
        continuation_count,
    })
}

/// Checks each index against the physical-index encoder, the primary count,
/// and the name-order rule the logical records will follow.
fn validate_indexes(
    spec: &TableSchemaSpec<'_>,
    plan: &TableSchemaPlan,
) -> Result<(), TableSchemaPlanError> {
    let mut primary: Option<usize> = None;
    for (position, planned) in spec.indexes.iter().enumerate() {
        let ordinal = position as u16;
        validate_name_bytes("logical index", position, planned.name)?;
        validate_name(
            "logical index",
            ordinal,
            planned.name,
            spec.indexes[..position].iter().map(|earlier| earlier.name),
        )
        .map_err(TableSchemaPlanError::Definition)?;
        let Some((root, row)) = plan.index_placements().nth(position) else {
            return Err(TableSchemaPlanError::UnobservedIndexCount {
                count: spec.indexes.len(),
                observed: MAX_OBSERVED_INDEXES,
            });
        };
        let physical = PhysicalIndexSpec {
            fields: planned.fields,
            usage_map_page: plan.map_page(),
            usage_map_row: row,
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
        for (earlier, other) in spec.indexes[..position].iter().enumerate() {
            if other.name.cmp(planned.name)
                != case_folded(other.name).cmp(case_folded(planned.name))
            {
                return Err(TableSchemaPlanError::UnderdeterminedIndexNameOrder {
                    first: earlier,
                    second: position,
                });
            }
        }
    }
    Ok(())
}

/// Returns the logical (name-ordered) positions of `indexes` as physical
/// ordinals, the order `EXP-0093` observed logical records to take.
///
/// Names are compared by byte value; the planner has already refused pairs
/// whose byte order and ASCII case-folded order disagree.
pub(crate) fn logical_index_order(indexes: &[PlannedIndex<'_>]) -> Vec<usize> {
    let mut order: Vec<usize> = (0..indexes.len()).collect();
    order.sort_by(|left, right| indexes[*left].name.cmp(indexes[*right].name));
    order
}

fn case_folded(name: &[u8]) -> impl Iterator<Item = u8> + '_ {
    name.iter().map(u8::to_ascii_lowercase)
}

#[cfg(test)]
#[path = "table_schema_plan_tests.rs"]
mod tests;
