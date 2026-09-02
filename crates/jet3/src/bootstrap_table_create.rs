//! Pages a planned user-table create appends to the empty database, derived
//! from the `EXP-0087` create observations rather than recorded bytes.
//!
//! `EXP-0087` observed each create append a definition root, a page of
//! usage-map rows, an index root directly after it when the table carries an
//! index, and, on a database's first create only, a long-value page holding
//! the catalog row's `LvProp` payload. No observed first create carried an
//! index, so the long-value page's position after an index root is deduced:
//! the index root was observed directly after the map page, so the long-value
//! page can only follow it.
//!
//! The map page carries the table's owned and available maps at rows 0 and 1
//! (`EXP-0073`), then either the index's map row at row 2 holding the index
//! root (`EXP-0073`, from `MSysACEs` and the `Alpha` index transition) or one
//! owned/available pair at rows 2 and 3 for a single Memo or LongBinary
//! column (`EXP-0077`). No observed create carried both an index and a
//! long-value column, or more than one long-value column, so those layouts
//! are refused rather than invented.
//!
//! `EXP-0087` pinned the `LvProp` payload framing but no grammar, so the
//! caller supplies the payload and this module checks only the framing. Only
//! the `Alpha(Id Long)` payload `EXP-0079` recorded is established.
//!
//! Column types, column counts, and index field counts beyond the four
//! observed creates rely on the encoders' established record formats rather
//! than on an observed create.

use super::*;
use crate::long_value_writer::{external_long_value_header, validate_single_page_row};
use crate::table_schema_plan::{
    PlannedIndexKind, TableSchemaPlan, TableSchemaSpec, plan_table_schema,
};
use crate::{ExternalLongValueStorage, LogicalIndexSpec, RowLocator};

/// `EXP-0073`: row of the map page holding the table's owned-page map.
const OWNED_MAP_ROW: u8 = 0;
/// `EXP-0073`: row of the map page holding the table's available-page map.
const AVAILABLE_MAP_ROW: u8 = 1;
/// First map-page row after the table's own two maps.
const FIRST_FREE_MAP_ROW: u8 = 2;
/// `EXP-0087`: `MSysObjects` `Flags` of a created user table.
const USER_FLAGS: i32 = 0;
/// Long-value column count `EXP-0087` observed on a created table.
const MAX_OBSERVED_LONG_VALUE_COLUMNS: usize = 1;
/// `EXP-0087`: every `LvProp` payload began with these bytes.
const PROPERTY_MAGIC: [u8; 4] = *b"KKD\x00";
/// `EXP-0087`: a four-byte inclusive length then a two-byte kind per chunk.
const PROPERTY_CHUNK_HEADER_LEN: usize = 6;
/// `EXP-0087`: the kind of the one leading chunk of every payload.
const PROPERTY_NAMES_CHUNK_KIND: u16 = 0x0080;

/// One user table to create in the empty database.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct TableCreate<'a> {
    /// The table's name, columns, and indexes.
    pub(crate) spec: &'a TableSchemaSpec<'a>,
    /// The catalog row's `LvProp` payload, whose grammar `EXP-0087` leaves
    /// unestablished beyond the framing this module checks.
    pub(crate) properties: &'a [u8],
}

/// A create with its pages assigned and its map-page rows laid out.
#[derive(Debug, Clone)]
pub(super) struct PlannedCreate<'a> {
    create: TableCreate<'a>,
    plan: TableSchemaPlan,
    /// The indexed column's root and the map-page row of its usage map.
    index: Option<(PageNumber, u8)>,
    /// The long-value column's ordinal and the map-page row of its owned
    /// map; its available map is the next row.
    long_value: Option<(u16, u8)>,
    long_value_page: u64,
}

impl<'a> PlannedCreate<'a> {
    /// Plans `create` as the first create in the empty database.
    pub(super) fn new(create: TableCreate<'a>) -> Result<Self, BootstrapComposeError> {
        let plan = plan_table_schema(create.spec, EMPTY_DATABASE_PAGE_COUNT)?;
        validate_single_page_row(create.properties)?;
        validate_property_framing(create.properties, create.spec.columns.len())?;
        let mut long_value_columns = long_value_columns(create.spec);
        let long_value_column = long_value_columns.next();
        if long_value_columns.next().is_some() {
            return Err(BootstrapComposeError::UnobservedLongValueColumnCount {
                observed: MAX_OBSERVED_LONG_VALUE_COLUMNS,
            });
        }
        let index = plan.index_root().map(|root| (root, FIRST_FREE_MAP_ROW));
        let long_value = match (index, long_value_column) {
            (Some(_), Some(_)) => return Err(BootstrapComposeError::UnobservedMapRowLayout),
            (None, Some(column)) => Some((column, FIRST_FREE_MAP_ROW)),
            (_, None) => None,
        };
        let long_value_page = plan.definition_root().get() + plan.appended_page_count();
        Ok(Self {
            create,
            plan,
            index,
            long_value,
            long_value_page,
        })
    }

    pub(super) const fn object_id(&self) -> i32 {
        self.plan.object_id()
    }

    pub(super) const fn long_value_page(&self) -> u64 {
        self.long_value_page
    }

    /// Returns the page count once every appended page is in place.
    pub(super) const fn page_count(&self) -> u64 {
        self.long_value_page + 1
    }

    /// Returns the catalog row the create adds (`EXP-0087`).
    pub(super) const fn catalog_seed(&self) -> CatalogSeed<'a> {
        CatalogSeed {
            id: self.plan.object_id(),
            parent: TABLES_ID,
            name: self.create.spec.name,
            kind: 1,
            owner: CATALOG_OWNER_0301,
            flags: USER_FLAGS,
        }
    }

    /// Returns the two access-control rows the create adds (`EXP-0087`).
    pub(super) const fn ace_seeds(&self) -> [AceSeed; 2] {
        let id = self.plan.object_id();
        [
            ace(id, b"\x03\x01", 983294, false),
            ace(id, b"\x02\x01", 1048319, false),
        ]
    }

    /// Returns the catalog row's `LvProp` reference to the long-value page.
    pub(super) fn long_value_header(&self) -> Result<[u8; 12], BootstrapComposeError> {
        Ok(external_long_value_header(
            self.create.properties.len(),
            ExternalLongValueStorage::SinglePage,
            RowLocator::new(PageNumber::new(self.long_value_page), 0),
        )?)
    }

    /// Appends the create's pages: definition root, map page, index root when
    /// indexed, then the long-value page.
    pub(super) fn append_pages(
        &self,
        plan: &mut WholeFileImagePlan,
        budget: &mut ResourceBudget,
    ) -> Result<(), BootstrapComposeError> {
        let mut append_map = global_map(EMPTY_DATABASE_PAGE_COUNT, budget)?;
        plan.append(self.definition_page(budget)?, &mut append_map, budget)?;
        plan.append(self.map_page(budget)?, &mut append_map, budget)?;
        if self.index.is_some() {
            let owner = self.plan.definition_root().get();
            plan.append(empty_index_page(owner, budget)?, &mut append_map, budget)?;
        }
        plan.append(self.long_value_page_image(budget)?, &mut append_map, budget)?;
        Ok(())
    }

    fn definition_page(
        &self,
        budget: &mut ResourceBudget,
    ) -> Result<PageImage, BootstrapComposeError> {
        let map = self.plan.map_page();
        let spec = self.create.spec;
        let physical = self
            .index
            .into_iter()
            .zip(spec.indexes)
            .map(|((root, row), index)| PhysicalIndexSpec {
                fields: index.fields,
                usage_map_page: map,
                usage_map_row: row,
                root,
                flags: index.kind.flags(),
                entry_count: 0,
            })
            .collect::<Vec<_>>();
        let logical = spec
            .indexes
            .iter()
            .enumerate()
            .map(|(ordinal, index)| {
                Ok(LogicalIndexSpec {
                    name: index.name,
                    physical_index: u16::try_from(ordinal).map_err(|_| {
                        Error::IntegerConversion {
                            value: ordinal as u128,
                            target: "u16",
                        }
                    })?,
                    kind: match index.kind {
                        PlannedIndexKind::Primary => LogicalIndexKindSpec::Primary,
                        PlannedIndexKind::Ordinary => LogicalIndexKindSpec::Ordinary,
                    },
                })
            })
            .collect::<Result<Vec<_>, BootstrapComposeError>>()?;
        let long_value_maps = self
            .long_value
            .map(|(column, owned)| LongValueMapSpec {
                column,
                owned: MapRowLocator::new(map, owned),
                available: MapRowLocator::new(map, owned + 1),
            })
            .into_iter()
            .collect::<Vec<_>>();
        definition_page(
            &TableDefinitionSpec {
                kind: TableDefinitionKind::User,
                columns: spec.columns,
                system_column_classes: &[],
                physical_indexes: &physical,
                indexes: &logical,
                owned_map: MapRowLocator::new(map, OWNED_MAP_ROW),
                available_map: MapRowLocator::new(map, AVAILABLE_MAP_ROW),
                row_count: 0,
                long_value_maps: &long_value_maps,
            },
            budget,
        )
    }

    /// Builds the map page with the rows `definition_page` names.
    fn map_page(&self, budget: &mut ResourceBudget) -> Result<PageImage, BootstrapComposeError> {
        let empty = inline_map_row(&[], budget)?;
        let mut rows: Vec<[u8; 133]> = vec![empty, empty];
        if let Some((root, row)) = self.index {
            debug_assert_eq!(usize::from(row), rows.len());
            rows.push(inline_map_row(&[root.get()], budget)?);
        }
        if let Some((_, owned)) = self.long_value {
            debug_assert_eq!(usize::from(owned), rows.len());
            rows.push(empty);
            rows.push(empty);
        }
        let rows = rows.iter().map(<[u8; 133]>::as_slice).collect::<Vec<_>>();
        data_page(HEADER_PAGE, &rows, budget)
    }

    fn long_value_page_image(
        &self,
        budget: &mut ResourceBudget,
    ) -> Result<PageImage, BootstrapComposeError> {
        let mut builder = DataPageBuilder::new_long_value(budget)?;
        builder.append_row(self.create.properties, budget)?;
        finish_data_builder(builder, budget)
    }
}

/// Returns the ordinals of the columns that own long-value map groups.
fn long_value_columns<'s>(spec: &'s TableSchemaSpec<'_>) -> impl Iterator<Item = u16> + 's {
    spec.columns
        .iter()
        .enumerate()
        .filter(|(_, column)| {
            matches!(
                column.physical_type(),
                ColumnPhysicalType::Memo | ColumnPhysicalType::LongBinary
            )
        })
        .filter_map(|(ordinal, _)| u16::try_from(ordinal).ok())
}

/// Checks `payload` against the `EXP-0087` framing: the magic, exact
/// coverage by length-prefixed chunks, one leading names chunk, then one
/// chunk per column. Chunk bodies stay uninterpreted.
fn validate_property_framing(
    payload: &[u8],
    column_count: usize,
) -> Result<(), BootstrapComposeError> {
    let malformed = |offset: usize| BootstrapComposeError::PropertyFraming { offset };
    let Some(mut rest) = payload.strip_prefix(&PROPERTY_MAGIC) else {
        return Err(malformed(0));
    };
    let mut offset = PROPERTY_MAGIC.len();
    let mut chunks = 0_usize;
    while !rest.is_empty() {
        let (header, _) = rest
            .split_at_checked(PROPERTY_CHUNK_HEADER_LEN)
            .ok_or(malformed(offset))?;
        let length = u32::from_le_bytes([header[0], header[1], header[2], header[3]]);
        let kind = u16::from_le_bytes([header[4], header[5]]);
        let length = usize::try_from(length)
            .ok()
            .filter(|length| *length >= PROPERTY_CHUNK_HEADER_LEN && *length <= rest.len())
            .ok_or(malformed(offset))?;
        if chunks == 0 && kind != PROPERTY_NAMES_CHUNK_KIND {
            return Err(malformed(offset));
        }
        rest = &rest[length..];
        offset += length;
        chunks += 1;
    }
    if chunks != column_count + 1 {
        return Err(BootstrapComposeError::PropertyChunkCount {
            chunks,
            expected: column_count + 1,
        });
    }
    Ok(())
}

#[cfg(test)]
#[path = "bootstrap_table_create_tests.rs"]
mod tests;
