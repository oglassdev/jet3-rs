//! Pages a planned user-table create appends to the empty database, derived
//! from the `EXP-0093` and `EXP-0105` first-create observations rather than
//! recorded bytes.
//!
//! `EXP-0093` observed each first create append a definition root, a page of
//! usage-map rows, a long-value page for the catalog row's `LvProp`, then one
//! empty index root per physical index in append order. The map page carries
//! the table's owned and available maps at rows 0 and 1, then one row per
//! index at `2 + physical_ordinal` mapping only that index's root. Logical
//! index records appear in name order while referring back to the physical
//! ordinals.
//!
//! The planner refuses definitions that need a continuation page, because
//! `EXP-0105` established their capacities but not their placement.
//!
//! `EXP-0091` accepted the exact `Alpha(Id Long)` first create with a null
//! catalog `LvProp` and a retained, mapped, empty long-value page. Every
//! create composed here takes that null form, and no `LvProp` payload is
//! written, because no experiment established a payload grammar a caller
//! could satisfy. For any schema other than `Alpha` this is an unvalidated
//! candidate construction: `EXP-0093`'s indexed creates carried a provider
//! written payload, and `EXP-0091` explicitly does not extend null
//! acceptance to arbitrary schemas. Only a DAO differential can.
//!
//! One Memo or LongBinary column takes one owned/available map-row pair at
//! rows 2 and 3 (`EXP-0077`). No observed create carried both an index and a
//! long-value column, or more than one long-value column, so those layouts
//! are refused rather than invented.

use super::*;
use crate::table_schema_plan::{
    AVAILABLE_MAP_ROW, FIRST_INDEX_MAP_ROW, OWNED_MAP_ROW, TableSchemaPlan, TableSchemaSpec,
    logical_index_order, plan_table_schema,
};

/// `EXP-0093`: `MSysObjects` `Flags` of a created user table.
const USER_FLAGS: i32 = 0;
/// Long-value column count `EXP-0087` observed on a created table.
const MAX_OBSERVED_LONG_VALUE_COLUMNS: usize = 1;

/// A create with its pages assigned and its map-page rows laid out.
#[derive(Debug, Clone)]
pub(super) struct PlannedCreate<'a> {
    spec: &'a TableSchemaSpec<'a>,
    plan: TableSchemaPlan,
    /// The long-value column's ordinal; its owned map takes the first free
    /// map-page row and its available map the next.
    long_value: Option<u16>,
}

impl<'a> PlannedCreate<'a> {
    /// Plans `spec` as the first create in the empty database.
    pub(super) fn new(spec: &'a TableSchemaSpec<'a>) -> Result<Self, BootstrapComposeError> {
        let plan = plan_table_schema(spec, EMPTY_DATABASE_PAGE_COUNT)?;
        let mut long_value_columns = long_value_columns(spec);
        let long_value = long_value_columns.next();
        if long_value_columns.next().is_some() {
            return Err(BootstrapComposeError::UnobservedLongValueColumnCount {
                observed: MAX_OBSERVED_LONG_VALUE_COLUMNS,
            });
        }
        if long_value.is_some() && !spec.indexes.is_empty() {
            return Err(BootstrapComposeError::UnobservedMapRowLayout);
        }
        Ok(Self {
            spec,
            plan,
            long_value,
        })
    }

    pub(super) const fn object_id(&self) -> i32 {
        self.plan.object_id()
    }

    /// Returns the page holding the catalog row's `LvProp` long value.
    pub(super) const fn property_page(&self) -> u64 {
        self.plan.property_page().get()
    }

    /// Returns the page count once every appended page is in place.
    pub(super) const fn page_count(&self) -> u64 {
        self.plan.definition_root().get() + self.plan.appended_page_count()
    }

    /// Returns the catalog row the create adds (`EXP-0087`).
    pub(super) const fn catalog_seed(&self) -> CatalogSeed<'a> {
        CatalogSeed {
            id: self.plan.object_id(),
            parent: TABLES_ID,
            name: self.spec.name,
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

    /// Appends the create's pages in `EXP-0093` order: definition root, map
    /// page, long-value page, then the index roots.
    pub(super) fn append_pages(
        &self,
        plan: &mut WholeFileImagePlan,
        budget: &mut ResourceBudget,
    ) -> Result<(), BootstrapComposeError> {
        let mut append_map = global_map(EMPTY_DATABASE_PAGE_COUNT, budget)?;
        plan.append(self.definition_page(budget)?, &mut append_map, budget)?;
        plan.append(self.map_page(budget)?, &mut append_map, budget)?;
        let lval = DataPageBuilder::new_long_value(budget)?;
        plan.append(finish_data_builder(lval, budget)?, &mut append_map, budget)?;
        let owner = self.plan.definition_root().get();
        for _ in self.plan.index_placements() {
            plan.append(empty_index_page(owner, budget)?, &mut append_map, budget)?;
        }
        Ok(())
    }

    fn definition_page(
        &self,
        budget: &mut ResourceBudget,
    ) -> Result<PageImage, BootstrapComposeError> {
        let map = self.plan.map_page();
        let spec = self.spec;
        // The planner validated one placement per index, so the zip is exact.
        let physical = self
            .plan
            .index_placements()
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
        let logical = logical_index_order(spec.indexes)
            .into_iter()
            .map(|ordinal| {
                let index = &spec.indexes[ordinal];
                Ok(LogicalIndexSpec {
                    name: index.name,
                    physical_index: u16::try_from(ordinal).map_err(|_| {
                        Error::IntegerConversion {
                            value: ordinal as u128,
                            target: "u16",
                        }
                    })?,
                    kind: index.kind.logical_kind(),
                })
            })
            .collect::<Result<Vec<_>, BootstrapComposeError>>()?;
        let long_value_maps = self
            .long_value
            .map(|column| LongValueMapSpec {
                column,
                owned: MapRowLocator::new(map, FIRST_INDEX_MAP_ROW),
                available: MapRowLocator::new(map, FIRST_INDEX_MAP_ROW + 1),
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

    /// Builds the map page with the rows the definition names.
    fn map_page(&self, budget: &mut ResourceBudget) -> Result<PageImage, BootstrapComposeError> {
        let empty = inline_map_row(&[], budget)?;
        let mut rows: Vec<[u8; 133]> = vec![empty, empty];
        for (root, _) in self.plan.index_placements() {
            rows.push(inline_map_row(&[root.get()], budget)?);
        }
        if self.long_value.is_some() {
            rows.push(empty);
            rows.push(empty);
        }
        let rows = rows.iter().map(<[u8; 133]>::as_slice).collect::<Vec<_>>();
        data_page(HEADER_PAGE, &rows, budget)
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

#[cfg(test)]
#[path = "bootstrap_table_create_tests.rs"]
mod tests;
