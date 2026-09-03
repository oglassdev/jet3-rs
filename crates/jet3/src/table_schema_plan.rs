//! Typed description and crate-private planning of one new user table as a
//! database's first create.
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
//! bytes and each continuation 2,040. It observed those pages well past the
//! appended run (`[20, 68]` and `[20, 219, 218]`) and establishes no
//! allocation rule for them, so this module measures the continuation count
//! at the established capacities and refuses any definition that needs one.
//!
//! Neither experiment establishes an `Id` allocation rule beyond the observed
//! equality with the definition root page.

use std::fmt;

use crate::catalog_name_key::{CatalogNameKeyError, validate_catalog_name};
use crate::catalog_record_writer::{CatalogRecordWriteError, catalog_record_len};
use crate::column_definition_writer::{KEY_SLOT_COUNT, PhysicalIndexSpec, validate_physical_index};
use crate::page_image::PAGE_BYTES;
use crate::table_definition_layout::{definition_len, validate_column_layout, validate_name};
use crate::{
    ColumnSpec, IndexDirection, IndexFieldSpec, LogicalIndexKindSpec, PageNumber,
    PhysicalIndexFlagsSpec, TableDefinitionKind, TableDefinitionWriteError,
};

/// Largest index count `EXP-0093` observed on a created table.
pub(crate) const MAX_OBSERVED_INDEXES: usize = 3;
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
pub(crate) const CONTINUATION_CAPACITY: usize = PAGE_BYTES - 8;
/// `EXP-0087`: highest name byte with an established catalog-key weight.
/// `EXP-0101` is bounded and does not widen this.
const LAST_ESTABLISHED_NAME_BYTE: u8 = 0x7e;

/// The uniqueness class of an index in a [`TableSpec`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum IndexKind {
    /// The table's primary index; `EXP-0093` observed physical flags `0x09`.
    Primary,
    /// A unique non-primary index; `EXP-0093` observed physical flags `0x01`.
    Unique,
    /// Any other index; `EXP-0093` observed physical flags `0x00`.
    Ordinary,
}

impl IndexKind {
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

/// A reference to one column of a [`TableSpec`], by position or by name.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ColumnRef<'a> {
    /// The column at this zero-based position in [`TableSpec::columns`].
    Ordinal(u16),
    /// The column whose raw name bytes equal these.
    Name(&'a [u8]),
}

impl ColumnRef<'_> {
    /// Returns the ordinal this reference names among `columns`, if any.
    pub(crate) fn resolve(self, columns: &[ColumnSpec<'_>]) -> Option<u16> {
        match self {
            Self::Ordinal(ordinal) => (usize::from(ordinal) < columns.len()).then_some(ordinal),
            Self::Name(name) => columns
                .iter()
                .position(|column| column.name() == name)
                .and_then(|position| u16::try_from(position).ok()),
        }
    }
}

impl From<u16> for ColumnRef<'_> {
    fn from(ordinal: u16) -> Self {
        Self::Ordinal(ordinal)
    }
}

impl<'a> From<&'a [u8]> for ColumnRef<'a> {
    fn from(name: &'a [u8]) -> Self {
        Self::Name(name)
    }
}

impl<'a, const N: usize> From<&'a [u8; N]> for ColumnRef<'a> {
    fn from(name: &'a [u8; N]) -> Self {
        Self::Name(name)
    }
}

/// One key column of an [`IndexSpec`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct IndexColumnSpec<'a> {
    /// The table column the key uses.
    pub column: ColumnRef<'a>,
    /// Key direction.
    pub direction: IndexDirection,
}

impl<'a> IndexColumnSpec<'a> {
    /// Describes an ascending key on `column`.
    #[must_use]
    pub fn ascending(column: impl Into<ColumnRef<'a>>) -> Self {
        Self {
            column: column.into(),
            direction: IndexDirection::Ascending,
        }
    }

    /// Describes a descending key on `column`.
    #[must_use]
    pub fn descending(column: impl Into<ColumnRef<'a>>) -> Self {
        Self {
            column: column.into(),
            direction: IndexDirection::Descending,
        }
    }
}

/// One index of a [`TableSpec`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct IndexSpec<'a> {
    /// Index name; bytes must be at most `0x7E`.
    pub name: &'a [u8],
    /// Ordered key columns.
    pub fields: &'a [IndexColumnSpec<'a>],
    /// The index's uniqueness class.
    pub kind: IndexKind,
}

/// One user table to create.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TableSpec<'a> {
    /// Table name; bytes must be at most `0x7E`.
    pub name: &'a [u8],
    /// The table's columns in ordinal order.
    pub columns: &'a [ColumnSpec<'a>],
    /// The table's indexes in physical (append) order; at most three.
    pub indexes: &'a [IndexSpec<'a>],
}

/// Structured failure while planning one new user table.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TableSchemaPlanError {
    /// The table name cannot be encoded into a catalog index key.
    TableNameKey(CatalogNameKeyError),
    /// The table name cannot be encoded into an `MSysObjects` row.
    TableNameRow(CatalogRecordWriteError),
    /// The columns or indexes cannot be encoded into a table definition.
    Definition(TableDefinitionWriteError),
    /// The table declares no columns.
    NoColumns,
    /// An index key names a column the table does not declare.
    UnknownIndexColumn {
        /// Position of the index in the spec.
        index: usize,
        /// Position of the key within the index.
        field: usize,
    },
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
    /// The definition needs continuation pages, whose placement `EXP-0105`
    /// observed only as the provider's own allocation and left unestablished.
    ContinuationPlacementUnestablished {
        /// Encoded definition length.
        length: usize,
        /// Continuations the definition needs at the established capacities.
        continuations: usize,
    },
    /// `EXP-0093` observed no create carrying this many indexes.
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
/// then the index roots.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct TableSchemaPlan {
    object_id: i32,
    definition_root: PageNumber,
    /// Each index's key fields with column references resolved to ordinals.
    index_fields: Vec<Vec<IndexFieldSpec>>,
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
        (0..self.index_fields.len()).map(move |ordinal| {
            (
                PageNumber::new(first_root + ordinal as u64),
                FIRST_INDEX_MAP_ROW + ordinal as u8,
            )
        })
    }

    /// Returns each index's key fields, resolved to column ordinals, in
    /// physical ordinal order.
    pub(crate) fn index_fields(&self) -> impl Iterator<Item = &[IndexFieldSpec]> {
        self.index_fields.iter().map(Vec::as_slice)
    }

    /// Returns how many pages the create appends.
    pub(crate) fn appended_page_count(&self) -> u64 {
        3 + self.index_fields.len() as u64
    }
}

/// Validates `spec` as a first create and assigns its appended pages starting
/// at `first_page`, the database's current page count.
pub(crate) fn plan_table_schema(
    spec: &TableSpec<'_>,
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
    let length = measure_definition(spec)?;
    let continuations = continuation_count(length);
    if continuations > 0 {
        return Err(TableSchemaPlanError::ContinuationPlacementUnestablished {
            length,
            continuations,
        });
    }
    let index_fields = resolve_index_fields(spec)?;
    let plan = assign_pages(spec, first_page, index_fields)?;
    validate_indexes(spec, &plan)?;
    Ok(plan)
}

/// Resolves every index key to a column ordinal. The key count is bounded
/// before anything is allocated; ordinal bounds are checked later by the
/// physical-index encoder.
fn resolve_index_fields(
    spec: &TableSpec<'_>,
) -> Result<Vec<Vec<IndexFieldSpec>>, TableSchemaPlanError> {
    spec.indexes
        .iter()
        .enumerate()
        .map(|(index, planned)| {
            if planned.fields.len() > KEY_SLOT_COUNT {
                return Err(TableSchemaPlanError::Definition(
                    TableDefinitionWriteError::TooManyKeyFields {
                        physical_index: index as u16,
                        count: planned.fields.len(),
                        maximum: KEY_SLOT_COUNT,
                    },
                ));
            }
            planned
                .fields
                .iter()
                .enumerate()
                .map(|(field, key)| match key.column {
                    ColumnRef::Ordinal(column) => Ok(IndexFieldSpec {
                        column,
                        direction: key.direction,
                    }),
                    ColumnRef::Name(_) => key
                        .column
                        .resolve(spec.columns)
                        .map(|column| IndexFieldSpec {
                            column,
                            direction: key.direction,
                        })
                        .ok_or(TableSchemaPlanError::UnknownIndexColumn { index, field }),
                })
                .collect()
        })
        .collect()
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
fn measure_definition(spec: &TableSpec<'_>) -> Result<usize, TableSchemaPlanError> {
    // The writer requires one long-value map group per Memo or LongBinary
    // column, so the group count follows from the columns.
    let long_value_maps = spec
        .columns
        .iter()
        .filter(|column| column.column_type().is_long_value())
        .count();
    definition_len(
        spec.columns,
        spec.indexes.iter().map(|index| index.name),
        spec.indexes.len(),
        long_value_maps,
    )
    .map_err(TableSchemaPlanError::Definition)
}

/// Returns how many continuation pages a definition of `length` bytes needs
/// at the `EXP-0105` capacities.
pub(crate) const fn continuation_count(length: usize) -> usize {
    length
        .saturating_sub(DEFINITION_ROOT_CAPACITY)
        .div_ceil(CONTINUATION_CAPACITY)
}

/// Assigns the appended page run, refusing numbers the encoders cannot name.
fn assign_pages(
    spec: &TableSpec<'_>,
    first_page: u64,
    index_fields: Vec<Vec<IndexFieldSpec>>,
) -> Result<TableSchemaPlan, TableSchemaPlanError> {
    let needed = 3 + spec.indexes.len() as u64;
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
        index_fields,
    })
}

/// Checks each index against the physical-index encoder, the primary count,
/// and the name-order rule the logical records will follow.
fn validate_indexes(
    spec: &TableSpec<'_>,
    plan: &TableSchemaPlan,
) -> Result<(), TableSchemaPlanError> {
    let mut primary: Option<usize> = None;
    for ((position, planned), fields) in spec.indexes.iter().enumerate().zip(plan.index_fields()) {
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
            fields,
            usage_map_page: plan.map_page(),
            usage_map_row: row,
            root,
            flags: planned.kind.flags(),
            entry_count: 0,
        };
        validate_physical_index(ordinal, &physical, spec.columns)
            .map_err(TableSchemaPlanError::Definition)?;
        if planned.kind == IndexKind::Primary
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
pub(crate) fn logical_index_order(indexes: &[IndexSpec<'_>]) -> Vec<usize> {
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
