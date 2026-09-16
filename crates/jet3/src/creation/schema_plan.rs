//! Typed description and crate-private planning of one new user table in a
//! database.
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
//! is `2 + physical_ordinal` on the first map page. EXP-0252 establishes
//! 32 indexes, ten components and independent map pages after its fifteen
//! map-row slots fill. Consecutive packed map pages are a construction policy.
//!
//! `EXP-0087` observed three further creates in the same database, each
//! appending a definition root numbered equal to its `Id`, then its map page,
//! then an index root when it carried one index, and no further `LvProp`
//! page. EXP-0266 adds single or chained property pages when a later table
//! carries explicit column options.
//! `EXP-0222` combines these page roles with the `EXP-0093` index placements
//! for up to three indexes on any table.
//!
//! `EXP-0059` and `EXP-0105` establish definition chains: the root holds
//! 2,048 logical bytes and each linked continuation holds 2,040. This planner
//! assigns continuations consecutively after the map and optional `LvProp`
//! page, before index roots. `EXP-0107` accepted one unindexed first-table
//! continuation; `EXP-0247` compares longer, later, indexed and populated
//! definitions, including an empty terminal continuation at exact payload
//! boundaries. Consecutive placement is a tested construction policy.
//!
//! Neither experiment establishes an `Id` allocation rule beyond the observed
//! equality with the definition root page.

use std::fmt;

use crate::catalog_name_key::{
    CatalogNameKeyError, catalog_names_equal, supported_name_byte, validate_catalog_name,
};
use crate::catalog_record_writer::{CatalogRecordWriteError, catalog_record_len};
use crate::column_definition_writer::{KEY_SLOT_COUNT, PhysicalIndexSpec, validate_physical_index};
use crate::page_image::PAGE_BYTES;
use crate::table_definition_layout::{definition_len, validate_column_layout, validate_name};
use crate::{IndexFieldSpec, PageNumber, TableDefinitionKind, TableDefinitionWriteError};

/// EXP-0252/0279: at most 32 logical indexes, including relationship aliases.
pub(crate) const MAX_OBSERVED_INDEXES: usize = 32;
/// EXP-0252: fifteen 133-byte maps plus two-byte slots fit after the page header.
pub(crate) const MAP_ROWS_PER_PAGE: usize = 15;
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

pub(crate) use super::{ColumnRef, TableSpec};
#[cfg(test)]
pub(crate) use super::{IndexKind, IndexSpec};
#[cfg(test)]
use crate::ColumnSpec;

/// Structured failure while planning one new user table.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TableSchemaPlanError {
    /// The shared work budget is exhausted.
    Resource(crate::Error),
    /// The table name cannot be encoded into a catalog index key.
    TableNameKey(CatalogNameKeyError),
    /// The table name cannot be encoded into an `MSysObjects` row.
    TableNameRow(CatalogRecordWriteError),
    /// The columns or indexes cannot be encoded into a table definition.
    Definition(TableDefinitionWriteError),
    /// The table declares no columns.
    NoColumns,
    /// A created object's name exceeds the EXP-0249 admitted length.
    NameTooLong {
        /// Table, column or logical index.
        role: &'static str,
        /// Requested byte length.
        length: usize,
        /// Largest admitted byte length for this role.
        maximum: usize,
    },
    /// An index key names a column the table does not declare.
    UnknownIndexColumn {
        /// Position of the index in the spec.
        index: usize,
        /// Position of the key within the index.
        field: usize,
    },
    /// A column or index name contains a byte outside the supported grammar.
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
    /// The index count exceeds the EXP-0252/0279 native limit.
    UnobservedIndexCount {
        /// Declared and additional logical index count.
        count: usize,
        /// Largest observed index count.
        observed: usize,
    },
    /// A primary index requested a policy other than requiring all key components.
    InvalidPrimaryNullPolicy,
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
            Self::Resource(source) => Some(source),
            Self::TableNameKey(source) => Some(source),
            Self::TableNameRow(source) => Some(source),
            Self::Definition(source) => Some(source),
            _ => None,
        }
    }
}

/// The validated page assignment for one new user table.
///
/// The appended run is the definition root, map pages, any `LvProp` pages,
/// definition continuations, then
/// the index roots.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct TableSchemaPlan {
    object_id: i32,
    definition_root: PageNumber,
    /// Number of single or chained catalog property pages.
    property_pages: usize,
    /// Exact logical length of the encoded definition.
    definition_len: usize,
    /// Table maps, index maps and independent long-value map pairs.
    map_rows: usize,
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

    pub(crate) const fn map_page_count(&self) -> usize {
        self.map_rows.div_ceil(MAP_ROWS_PER_PAGE)
    }

    /// Resolves a validated global map-row ordinal into its packed page and slot.
    pub(crate) const fn map_location(&self, row: usize) -> crate::MapRowLocator {
        crate::MapRowLocator::new(
            PageNumber::new(self.map_page().get() + (row / MAP_ROWS_PER_PAGE) as u64),
            (row % MAP_ROWS_PER_PAGE) as u8,
        )
    }

    /// Returns the first catalog property page (EXP-0093/0266).
    pub(crate) const fn property_page(&self) -> Option<PageNumber> {
        if self.property_pages != 0 {
            Some(PageNumber::new(
                self.definition_root.get() + 1 + self.map_page_count() as u64,
            ))
        } else {
            None
        }
    }

    pub(crate) const fn property_page_count(&self) -> usize {
        self.property_pages
    }

    /// Returns the first page after the root, map, and any `LvProp` pages.
    const fn after_fixed_pages(&self) -> u64 {
        self.definition_root.get() + 1 + self.map_page_count() as u64 + self.property_pages as u64
    }

    /// Returns the exact logical length of the encoded definition.
    pub(crate) const fn definition_len(&self) -> usize {
        self.definition_len
    }

    /// Returns the first continuation page, when the definition needs a chain.
    /// Consecutive continuation pages follow the fixed pages.
    pub(crate) fn continuation_page(&self) -> Option<PageNumber> {
        (continuation_count(self.definition_len) > 0)
            .then(|| PageNumber::new(self.after_fixed_pages()))
    }

    /// Returns each index's root page and global map-row ordinal in physical
    /// order (`EXP-0093`: roots follow the `LvProp` page, rows follow the
    /// table's own two maps).
    pub(crate) fn index_placements(&self) -> impl Iterator<Item = (PageNumber, u8)> {
        let first_root = self.after_fixed_pages() + continuation_count(self.definition_len) as u64;
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
        self.after_fixed_pages() - self.definition_root.get()
            + continuation_count(self.definition_len) as u64
            + self.index_fields.len() as u64
    }
}

/// Validates `spec` and assigns its appended pages starting at `first_page`,
/// the database's current page count. The first create reserves a property
/// page; explicit column options reserve payload pages on any table.
pub(crate) fn plan_table_schema(
    spec: &TableSpec<'_>,
    first_page: u64,
    first_create: bool,
    budget: &mut crate::ResourceBudget,
) -> Result<TableSchemaPlan, TableSchemaPlanError> {
    plan_table_schema_with_logical_index(spec, first_page, first_create, None, budget)
}

/// Adds the EXP-0059/0268 parent relationship record before assigning pages.
pub(crate) fn plan_table_schema_with_logical_index(
    spec: &TableSpec<'_>,
    first_page: u64,
    first_create: bool,
    extra_name: Option<&[u8]>,
    budget: &mut crate::ResourceBudget,
) -> Result<TableSchemaPlan, TableSchemaPlanError> {
    plan_table_schema_with_logical_names(
        spec,
        first_page,
        first_create,
        extra_name.as_slice(),
        budget,
    )
}

/// EXP-0273: distinct relationship records can share a physical index.
pub(crate) fn plan_table_schema_with_logical_names(
    spec: &TableSpec<'_>,
    first_page: u64,
    first_create: bool,
    extra_names: &[&[u8]],
    budget: &mut crate::ResourceBudget,
) -> Result<TableSchemaPlan, TableSchemaPlanError> {
    budget
        .charge_work_units(
            (spec.columns.len().min(255) as u64)
                .saturating_add(spec.indexes.len().min(32) as u64)
                .saturating_add(extra_names.len() as u64)
                .saturating_mul(512),
        )
        .map_err(TableSchemaPlanError::Resource)?;
    validate_table_name(spec.name)?;
    if spec.columns.is_empty() {
        return Err(TableSchemaPlanError::NoColumns);
    }
    if spec.columns.len() > usize::from(u8::MAX) {
        return Err(TableSchemaPlanError::Definition(
            TableDefinitionWriteError::TooManyColumns {
                count: spec.columns.len(),
                maximum: usize::from(u8::MAX),
            },
        ));
    }
    for (ordinal, column) in spec.columns.iter().enumerate() {
        validate_name_length("column", column.name(), 64)?;
        validate_name_bytes("column", ordinal, column.name())?;
        validate_distinct_name(
            "column",
            ordinal,
            column.name(),
            spec.columns[..ordinal].iter().map(|c| c.name()),
            budget,
        )?;
    }
    validate_column_layout(spec.columns, TableDefinitionKind::User, &[])
        .map_err(TableSchemaPlanError::Definition)?;
    let count = spec.indexes.len().saturating_add(extra_names.len());
    if count > MAX_OBSERVED_INDEXES {
        return Err(TableSchemaPlanError::UnobservedIndexCount {
            count,
            observed: MAX_OBSERVED_INDEXES,
        });
    }
    for (position, &name) in extra_names.iter().enumerate() {
        let ordinal = spec.indexes.len() + position;
        validate_name_length("logical index", name, 63)?;
        if !super::relationship_name::HiddenName::matches(name) {
            validate_name_bytes("logical index", ordinal, name)?;
        }
        let prior = spec
            .indexes
            .iter()
            .map(|index| index.name)
            .chain(extra_names[..position].iter().copied());
        validate_name("logical index", ordinal as u16, name, prior.clone())
            .map_err(TableSchemaPlanError::Definition)?;
        validate_distinct_name("logical index", ordinal, name, prior.clone(), budget)?;
    }
    let length = measure_definition(spec, extra_names)?;
    let index_fields = resolve_index_fields(spec)?;
    let plan = assign_pages(spec, first_page, first_create, length, index_fields)?;
    validate_indexes(spec, &plan, budget)?;
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
    validate_name_length("table", name, 64)?;
    validate_catalog_name(name).map_err(TableSchemaPlanError::TableNameKey)?;
    catalog_record_len(name.len()).map_err(TableSchemaPlanError::TableNameRow)?;
    Ok(())
}

// EXP-0249 semantic creation limits are narrower than the stored length byte.
fn validate_name_length(
    role: &'static str,
    name: &[u8],
    maximum: usize,
) -> Result<(), TableSchemaPlanError> {
    if name.len() > maximum {
        return Err(TableSchemaPlanError::NameTooLong {
            role,
            length: name.len(),
            maximum,
        });
    }
    Ok(())
}

/// EXP-0277: object names share the defined CP1252 grammar and reject leading spaces.
fn validate_name_bytes(
    role: &'static str,
    ordinal: usize,
    name: &[u8],
) -> Result<(), TableSchemaPlanError> {
    match name.iter().enumerate().position(|(position, byte)| {
        !supported_name_byte(*byte) || (position == 0 && *byte == b' ')
    }) {
        Some(position) => Err(TableSchemaPlanError::NameByteUnestablished {
            role,
            ordinal,
            position,
            byte: name[position],
        }),
        None => Ok(()),
    }
}

fn validate_distinct_name<'a>(
    role: &'static str,
    ordinal: usize,
    name: &[u8],
    earlier: impl Iterator<Item = &'a [u8]>,
    budget: &mut crate::ResourceBudget,
) -> Result<(), TableSchemaPlanError> {
    for other in earlier {
        if other.len() > 64 {
            continue;
        }
        budget
            .charge_work_units(((name.len().min(64) + other.len().min(64)) as u64) * 2 + 1)
            .map_err(TableSchemaPlanError::Resource)?;
        let equal = if name.is_ascii()
            && other.is_ascii()
            && !name.ends_with(b" ")
            && !other.ends_with(b" ")
        {
            name.eq_ignore_ascii_case(other)
        } else {
            budget
                .charge_work_units(512)
                .map_err(TableSchemaPlanError::Resource)?;
            catalog_names_equal(other, name)
        };
        if equal {
            return Err(TableSchemaPlanError::Definition(
                TableDefinitionWriteError::DuplicateName {
                    role,
                    ordinal: ordinal as u16,
                },
            ));
        }
    }
    Ok(())
}

/// Returns the exact logical length of the definition `spec` encodes to.
fn measure_definition(
    spec: &TableSpec<'_>,
    extra_names: &[&[u8]],
) -> Result<usize, TableSchemaPlanError> {
    let long_value_maps = spec
        .columns
        .iter()
        .filter(|column| column.column_type().is_long_value())
        .count();
    definition_len(
        spec.columns,
        (0..spec.indexes.len() + extra_names.len()).map(|position| {
            if position < spec.indexes.len() {
                spec.indexes[position].name
            } else {
                extra_names[position - spec.indexes.len()]
            }
        }),
        spec.indexes.len(),
        long_value_maps,
    )
    .map_err(TableSchemaPlanError::Definition)
}

/// Returns how many continuation pages a definition of `length` bytes needs
/// at the `EXP-0105` capacities, retaining the `EXP-0247` empty terminal page
/// when the logical definition ends exactly at a payload boundary.
pub(crate) const fn continuation_count(length: usize) -> usize {
    if length < DEFINITION_ROOT_CAPACITY {
        0
    } else {
        1 + (length - DEFINITION_ROOT_CAPACITY) / CONTINUATION_CAPACITY
    }
}

/// Assigns the appended page run, refusing numbers the encoders cannot name.
fn assign_pages(
    spec: &TableSpec<'_>,
    first_page: u64,
    first_create: bool,
    definition_len: usize,
    index_fields: Vec<Vec<IndexFieldSpec>>,
) -> Result<TableSchemaPlan, TableSchemaPlanError> {
    let map_rows = 2
        + spec.indexes.len()
        + 2 * spec
            .columns
            .iter()
            .filter(|column| column.column_type().is_long_value())
            .count();
    let map_pages = map_rows.div_ceil(MAP_ROWS_PER_PAGE);
    // EXP-0266: column properties also occur on later tables and can be chained.
    let property_pages = crate::column_properties::ColumnProperties::new(spec.columns).map_or(
        usize::from(first_create),
        |properties| {
            if properties.len() <= crate::long_value_writer::MAX_SINGLE_PAGE_PAYLOAD {
                1
            } else {
                properties
                    .len()
                    .div_ceil(crate::long_value_writer::MAX_CHAINED_FRAGMENT)
            }
        },
    );
    let needed = 1
        + map_pages as u64
        + property_pages as u64
        + continuation_count(definition_len) as u64
        + spec.indexes.len() as u64;
    // `EXP-0093` numbers the object equal to its definition root, and
    // `MSysObjects.Id` is a signed Long, so the run must stay in that range.
    let object_id = i32::try_from(first_page)
        .ok()
        .filter(|_| first_page.checked_add(needed).is_some())
        .ok_or(TableSchemaPlanError::PageOverflow {
            first: first_page,
            needed,
        })?;
    let map_page = first_page + map_pages as u64;
    if map_page > MAX_MAP_PAGE {
        return Err(TableSchemaPlanError::MapPageNotAddressable {
            page: map_page,
            maximum: MAX_MAP_PAGE,
        });
    }
    Ok(TableSchemaPlan {
        object_id,
        definition_root: PageNumber::new(first_page),
        property_pages,
        definition_len,
        map_rows,
        index_fields,
    })
}

/// Checks each index against the physical-index encoder and the primary count.
fn validate_indexes(
    spec: &TableSpec<'_>,
    plan: &TableSchemaPlan,
    budget: &mut crate::ResourceBudget,
) -> Result<(), TableSchemaPlanError> {
    let mut primary: Option<usize> = None;
    for ((position, planned), fields) in spec.indexes.iter().enumerate().zip(plan.index_fields()) {
        let ordinal = position as u16;
        // EXP-0249: native 64-byte index names fail Seek; 63 passed.
        validate_name_length("logical index", planned.name, 63)?;
        validate_name_bytes("logical index", position, planned.name)?;
        validate_distinct_name(
            "logical index",
            position,
            planned.name,
            spec.indexes[..position].iter().map(|earlier| earlier.name),
            budget,
        )?;
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
        if planned.kind.is_primary()
            && planned.kind.null_policy() != crate::IndexNullPolicy::Required
        {
            return Err(TableSchemaPlanError::InvalidPrimaryNullPolicy);
        }
        let physical = PhysicalIndexSpec {
            fields,
            usage_map_page: plan.map_location(usize::from(row)).page(),
            usage_map_row: plan.map_location(usize::from(row)).row(),
            root,
            flags: planned.kind.flags(),
            entry_count: 0,
        };
        validate_physical_index(ordinal, &physical, spec.columns)
            .map_err(TableSchemaPlanError::Definition)?;
        if planned.kind.is_primary()
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
#[path = "schema_plan_tests.rs"]
mod tests;
