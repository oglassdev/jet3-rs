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
//! `EXP-0105` observed a definition longer than its root page continuing on
//! one or two further definition pages, chained through the next-page
//! reference at `[4,8)`. The root holds 2,048 logical bytes and each
//! continuation 2,040 after a repeated prefix and its own next-page
//! reference. Only the pointer order is observed; the continuation pages are
//! placed directly after the long-value page and marked globally in use.
//!
//! `EXP-0091` accepted a first create whose catalog `LvProp` is null while
//! the long-value page stays present and mapped. That bounded result is the
//! basis for every composed create here; no `LvProp` payload is written.
//!
//! One Memo or LongBinary column takes one owned/available map-row pair at
//! rows 2 and 3 (`EXP-0077`). No observed create carried both an index and a
//! long-value column, or more than one long-value column, so those layouts
//! are refused rather than invented.

use super::*;
use crate::table_schema_plan::{
    AVAILABLE_MAP_ROW, CONTINUATION_CAPACITY, CONTINUATION_PAYLOAD_OFFSET,
    DEFINITION_ROOT_CAPACITY, FIRST_INDEX_MAP_ROW, MAX_OBSERVED_CONTINUATIONS, OWNED_MAP_ROW,
    TableSchemaPlan, TableSchemaSpec, logical_index_order, plan_table_schema,
};

/// `EXP-0093`: `MSysObjects` `Flags` of a created user table.
const USER_FLAGS: i32 = 0;
/// Long-value column count `EXP-0087` observed on a created table.
const MAX_OBSERVED_LONG_VALUE_COLUMNS: usize = 1;
/// Longest logical definition the planner admits.
const MAX_DEFINITION_LEN: usize =
    DEFINITION_ROOT_CAPACITY + MAX_OBSERVED_CONTINUATIONS * CONTINUATION_CAPACITY;
/// `EXP-0059`: the next-definition-page reference sits at `[4,8)`.
const NEXT_PAGE_OFFSET: usize = 4;
/// `EXP-0059`: the four-byte prefix every definition page repeats.
const PREFIX_LEN: usize = 4;

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
    /// page, long-value page, then the index roots; or, per `EXP-0105`, the
    /// definition continuation pages in ascending physical order.
    pub(super) fn append_pages(
        &self,
        plan: &mut WholeFileImagePlan,
        budget: &mut ResourceBudget,
    ) -> Result<(), BootstrapComposeError> {
        let mut append_map = global_map(EMPTY_DATABASE_PAGE_COUNT, budget)?;
        let (root, mut continuations) = self.definition_pages(budget)?;
        plan.append(root, &mut append_map, budget)?;
        plan.append(self.map_page(budget)?, &mut append_map, budget)?;
        let lval = DataPageBuilder::new_long_value(budget)?;
        plan.append(finish_data_builder(lval, budget)?, &mut append_map, budget)?;
        let owner = self.plan.definition_root().get();
        for _ in self.plan.index_placements() {
            plan.append(empty_index_page(owner, budget)?, &mut append_map, budget)?;
        }
        continuations.sort_by_key(|(page, _)| *page);
        for (_, image) in continuations {
            plan.append(image, &mut append_map, budget)?;
        }
        Ok(())
    }

    /// Encodes the definition and splits it into its root page and its
    /// continuation pages, each paired with its planned page number.
    fn definition_pages(
        &self,
        budget: &mut ResourceBudget,
    ) -> Result<(PageImage, Vec<(PageNumber, PageImage)>), BootstrapComposeError> {
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
        let mut logical_bytes = [0_u8; MAX_DEFINITION_LEN];
        let length = encode_table_definition(
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
            &mut logical_bytes,
            budget,
        )?
        .get() as usize;
        if length != self.plan.definition_len() {
            return Err(BootstrapComposeError::DefinitionLengthMismatch {
                planned: self.plan.definition_len(),
                encoded: length,
            });
        }
        split_definition(&logical_bytes[..length], self.plan.continuation_chain())
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

/// Splits the logical definition across its root page and the continuation
/// pages `chain` names in pointer order, filling each next-page reference.
fn split_definition(
    logical: &[u8],
    chain: impl Iterator<Item = PageNumber>,
) -> Result<(PageImage, Vec<(PageNumber, PageImage)>), BootstrapComposeError> {
    let chain = chain.collect::<Vec<_>>();
    let root_len = logical.len().min(DEFINITION_ROOT_CAPACITY);
    let mut root = [0_u8; PAGE_BYTES];
    root[..root_len].copy_from_slice(&logical[..root_len]);
    write_next_page(&mut root, chain.first().copied())?;
    let mut continuations = Vec::with_capacity(chain.len());
    let mut rest = &logical[root_len..];
    for (position, page) in chain.iter().enumerate() {
        let taken = rest.len().min(CONTINUATION_CAPACITY);
        let mut image = [0_u8; PAGE_BYTES];
        image[..PREFIX_LEN].copy_from_slice(&logical[..PREFIX_LEN]);
        write_next_page(&mut image, chain.get(position + 1).copied())?;
        image[CONTINUATION_PAYLOAD_OFFSET..CONTINUATION_PAYLOAD_OFFSET + taken]
            .copy_from_slice(&rest[..taken]);
        rest = &rest[taken..];
        continuations.push((*page, PageImage::from_bytes(image)));
    }
    if !rest.is_empty() {
        return Err(BootstrapComposeError::DefinitionLengthMismatch {
            planned: logical.len() - rest.len(),
            encoded: logical.len(),
        });
    }
    Ok((PageImage::from_bytes(root), continuations))
}

fn write_next_page(page: &mut [u8; PAGE_BYTES], next: Option<PageNumber>) -> Result<(), Error> {
    let next = next.map_or(Ok(0), |page| {
        u32::try_from(page.get()).map_err(|_| Error::IntegerConversion {
            value: u128::from(page.get()),
            target: "u32",
        })
    })?;
    page[NEXT_PAGE_OFFSET..NEXT_PAGE_OFFSET + 4].copy_from_slice(&next.to_le_bytes());
    Ok(())
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
