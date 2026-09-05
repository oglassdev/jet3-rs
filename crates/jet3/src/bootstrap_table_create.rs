//! Pages a planned user-table create appends to the database, derived from
//! the `EXP-0093` and `EXP-0105` first-create observations and the `EXP-0087`
//! later-create observations rather than recorded bytes.
//!
//! `EXP-0093` observed each first create append a definition root, a page of
//! usage-map rows, a long-value page for the catalog row's `LvProp`, then one
//! empty index root per physical index in append order. The map page carries
//! the table's owned and available maps at rows 0 and 1, then one row per
//! index at `2 + physical_ordinal` mapping only that index's root. Logical
//! index records appear in name order while referring back to the physical
//! ordinals.
//!
//! A definition longer than its root page takes one continuation page directly
//! after the `LvProp` page, the compact construction `EXP-0107` observed DAO
//! accept for an unindexed table. The root's bytes `[4,8)` name that page and
//! the continuation repeats the definition prefix, holds zero at `[4,8)`, and
//! carries the remaining logical bytes from offset 8 (`EXP-0059`,
//! `EXP-0105`). Longer chains, and a continuation beside an index, are refused
//! by the planner.
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
    AVAILABLE_MAP_ROW, CONTINUATION_CAPACITY, DEFINITION_ROOT_CAPACITY, FIRST_INDEX_MAP_ROW,
    OWNED_MAP_ROW, TableSchemaPlan, TableSpec, logical_index_order, plan_table_schema,
};

/// `EXP-0093`: `MSysObjects` `Flags` of a created user table.
const USER_FLAGS: i32 = 0;
/// Long-value column count `EXP-0087` observed on a created table.
const MAX_OBSERVED_LONG_VALUE_COLUMNS: usize = 1;

/// A create with its pages assigned and its map-page rows laid out.
#[derive(Debug, Clone)]
pub(super) struct PlannedCreate<'a> {
    spec: &'a TableSpec<'a>,
    plan: TableSchemaPlan,
    /// The long-value column's ordinal; its owned map takes the first free
    /// map-page row and its available map the next.
    long_value: Option<u16>,
    initial_data: Vec<InitialDataPage>,
    initial_long_values: Option<InitialLongValues>,
    initial_row_count: u32,
    initial_index: Option<InitialLongIndex>,
    initial_autoincrement: Option<InitialAutoIncrement>,
}

#[derive(Debug, Clone)]
struct InitialDataPage {
    image: PageImage,
    available: bool,
    rows: u16,
}

impl<'a> PlannedCreate<'a> {
    /// Plans `spec` as the create that appends at `first_page`; only the
    /// database's first create carries the `LvProp` page.
    pub(super) fn new(
        spec: &'a TableSpec<'a>,
        first_page: u64,
        first_create: bool,
    ) -> Result<Self, ComposeError> {
        let plan = plan_table_schema(spec, first_page, first_create)?;
        let mut long_value_columns = long_value_columns(spec);
        let long_value = long_value_columns.next();
        if long_value_columns.next().is_some() {
            return Err(ComposeError::UnobservedLongValueColumnCount {
                observed: MAX_OBSERVED_LONG_VALUE_COLUMNS,
            });
        }
        if long_value.is_some() && !spec.indexes.is_empty() {
            return Err(ComposeError::UnobservedMapRowLayout);
        }
        Ok(Self {
            spec,
            plan,
            long_value,
            initial_data: Vec::new(),
            initial_long_values: None,
            initial_row_count: 0,
            initial_index: None,
            initial_autoincrement: None,
        })
    }

    /// Packs rows using EXP-0060 encoding and EXP-0065 append placement.
    /// EXP-0057 supplies map roles; EXP-0073 supplies the definition row count.
    /// Multiple pages and their available-map membership remain a candidate
    /// hypothesis, with no generalized page-zero insertion transition.
    pub(super) fn with_rows(
        mut self,
        rows: &[&[RowValue<'_>]],
        budget: &mut ResourceBudget,
    ) -> Result<Self, ComposeError> {
        if self.plan.continuation_page().is_some() {
            return Err(ComposeError::UnsupportedInitialRowSchema);
        }
        let generated = InitialAutoIncrement::new(self.spec, rows.len())?;
        self.initial_autoincrement = generated;
        self.initial_index = InitialLongIndex::new(self.spec, rows.len(), budget)?;
        if rows.is_empty() {
            return Ok(self);
        }
        self.initial_row_count =
            u32::try_from(rows.len()).map_err(|_| Error::IntegerConversion {
                value: rows.len() as u128,
                target: "u32",
            })?;
        let layout = initial_row_layout(self.spec, budget)?;
        let mut next_payload = self.plan.definition_root().get() + self.plan.appended_page_count();
        if self.long_value.is_some() {
            self.initial_long_values = Some(InitialLongValues::new(next_payload, rows, budget)?);
        }
        let mut minimum = [0_u8; PAGE_BYTES];
        // EXP-0060 bounds column count to 255; fixed fields retain their width.
        let nulls = [RowValue::Null; u8::MAX as usize];
        let minimum_len =
            encode_row(&layout, &nulls[..layout.len()], &mut minimum, budget)?.get() as usize;
        let minimum = &minimum[..minimum_len];
        let mut builder = DataPageBuilder::new(self.plan.definition_root(), budget)?;
        let mut encoded = [0_u8; PAGE_BYTES];
        for (ordinal, row) in rows.iter().enumerate() {
            let mut lowered = [RowValue::Null; u8::MAX as usize];
            let row = if let Some(generated) = generated {
                generated.lower(row, ordinal, &mut lowered, budget)?;
                &lowered[..row.len()]
            } else {
                *row
            };
            let length = encode_initial_row(
                &layout,
                row,
                ordinal,
                &mut next_payload,
                &mut encoded,
                budget,
            )?
            .get() as usize;
            let bytes = &encoded[..length];
            let slot = match builder.append_row(bytes, budget) {
                Ok(slot) => slot,
                Err(PageImageError::PageFull { .. } | PageImageError::RowSlotsExhausted { .. })
                    if builder.row_count() != 0 =>
                {
                    self.push_initial_page(builder, minimum, budget)?;
                    builder = DataPageBuilder::new(self.plan.definition_root(), budget)?;
                    builder.append_row(bytes, budget)?
                }
                Err(error) => return Err(error.into()),
            };
            let locator = crate::RowLocator::new(PageNumber::new(self.page_count()), slot);
            if let Some(index) = &mut self.initial_index {
                index.push(row, locator, budget)?;
            }
        }
        if let Some(index) = &mut self.initial_index {
            index.sort(budget)?;
        }
        self.push_initial_page(builder, minimum, budget)?;
        Ok(self)
    }

    fn push_initial_page(
        &mut self,
        builder: DataPageBuilder,
        minimum_row: &[u8],
        budget: &mut ResourceBudget,
    ) -> Result<(), ComposeError> {
        let page = PageNumber::new(self.page_count());
        // The existing inline map covers this many pages (SRC-0020/EXP-0057).
        // EXP-0065 observed indirect growth, but supplies no general policy.
        let page_count = MAP_BITMAP_BYTES * 8;
        if page.get() >= page_count {
            return Err(UsageMapWriteError::PageOutOfMap {
                page,
                first: PageNumber::new(0),
                page_count,
            }
            .into());
        }
        // Candidate policy: only physically exhausted pages are unavailable.
        // This is not an inferred DAO free-space threshold (EXP-0057).
        let available = match builder.clone().append_row(minimum_row, budget) {
            Ok(_) => true,
            Err(PageImageError::PageFull { .. } | PageImageError::RowSlotsExhausted { .. }) => {
                false
            }
            Err(error) => return Err(error.into()),
        };
        if self.initial_data.len() == self.initial_data.capacity() {
            let capacity = self.initial_data.capacity();
            let additional = capacity.max(1);
            budget.charge_allocation(ByteCount::new(
                (additional * size_of::<InitialDataPage>()) as u64,
            ))?;
            self.initial_data
                .try_reserve_exact(additional)
                .map_err(|_| Error::Io {
                    operation: "reserve initial data pages",
                    kind: std::io::ErrorKind::OutOfMemory,
                })?;
        }
        let rows = builder.row_count();
        self.initial_data.push(InitialDataPage {
            image: finish_data_builder(builder, budget)?,
            available,
            rows,
        });
        Ok(())
    }

    /// Returns the page holding the catalog row's `LvProp` long value, present
    /// only on the database's first create.
    pub(super) fn property_page(&self) -> Option<u64> {
        self.plan.property_page().map(|page| page.get())
    }

    /// Returns the page count once every appended page is in place.
    pub(super) fn page_count(&self) -> u64 {
        self.plan.definition_root().get()
            + self.plan.appended_page_count()
            + self
                .initial_long_values
                .as_ref()
                .map_or(0, InitialLongValues::page_count)
            + self.initial_data.len() as u64
    }

    pub(super) const fn row_count(&self) -> u32 {
        self.initial_row_count
    }

    pub(super) fn index_distinct_count(&self) -> u32 {
        self.initial_index
            .as_ref()
            .map_or(0, InitialLongIndex::distinct_count)
    }

    /// Logical row locations in the same order used while packing initial data.
    pub(super) fn initial_row_locators(&self) -> impl Iterator<Item = crate::RowLocator> + '_ {
        let first = self.page_count() - self.initial_data.len() as u64;
        self.initial_data
            .iter()
            .enumerate()
            .flat_map(move |(page, data)| {
                (0..data.rows).map(move |slot| {
                    crate::RowLocator::new(PageNumber::new(first + page as u64), slot as u8)
                })
            })
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
    /// page, the first create's long-value page, the `EXP-0107` continuation
    /// if the definition needs one, then the index roots.
    pub(super) fn append_pages(
        &self,
        plan: &mut WholeFileImagePlan,
        append_map: &mut InlineUsageMapEncoder,
        budget: &mut ResourceBudget,
    ) -> Result<(), ComposeError> {
        let (root, continuation) = self.definition_pages(budget)?;
        plan.append(root, append_map, budget)?;
        plan.append(self.map_page(None, budget)?, append_map, budget)?;
        if self.plan.property_page().is_some() {
            let lval = DataPageBuilder::new_long_value(budget)?;
            plan.append(finish_data_builder(lval, budget)?, append_map, budget)?;
        }
        if let Some(continuation) = continuation {
            plan.append(continuation, append_map, budget)?;
        }
        let owner = self.plan.definition_root().get();
        for _ in self.plan.index_placements() {
            let image = if let Some(index) = &self.initial_index {
                index.image(self.plan.definition_root(), budget)?
            } else {
                empty_index_page(owner, budget)?
            };
            plan.append(image, append_map, budget)?;
        }
        if let Some(values) = &self.initial_long_values {
            values.append_pages(plan, append_map, budget)?;
        }
        for data in &self.initial_data {
            plan.append(data.image.clone(), append_map, budget)?;
        }
        Ok(())
    }

    /// Encodes the definition and splits it into its root page and, when the
    /// planner assigned one, its continuation page.
    fn definition_pages(
        &self,
        budget: &mut ResourceBudget,
    ) -> Result<(PageImage, Option<PageImage>), ComposeError> {
        let mut logical = [0_u8; DEFINITION_ROOT_CAPACITY + CONTINUATION_CAPACITY];
        let length = self.encode_definition(&mut logical, budget)?.get() as usize;
        if length != self.plan.definition_len() {
            return Err(ComposeError::DefinitionLengthMismatch {
                planned: self.plan.definition_len(),
                encoded: length,
            });
        }
        let mut root = [0_u8; PAGE_BYTES];
        root.copy_from_slice(&logical[..PAGE_BYTES]);
        if let Some(generated) = self.initial_autoincrement {
            generated.write(&mut root, budget)?;
        }
        let Some(continuation_page) = self.plan.continuation_page() else {
            return Ok((PageImage::from_bytes(root), None));
        };
        let next =
            u32::try_from(continuation_page.get()).map_err(|_| Error::IntegerConversion {
                value: u128::from(continuation_page.get()),
                target: "u32",
            })?;
        root[4..8].copy_from_slice(&next.to_le_bytes());
        let mut continuation = [0_u8; PAGE_BYTES];
        continuation[..4].copy_from_slice(&logical[..4]);
        let payload = &logical[DEFINITION_ROOT_CAPACITY..length];
        continuation[8..8 + payload.len()].copy_from_slice(payload);
        Ok((
            PageImage::from_bytes(root),
            Some(PageImage::from_bytes(continuation)),
        ))
    }

    fn encode_definition(
        &self,
        output: &mut [u8],
        budget: &mut ResourceBudget,
    ) -> Result<ByteCount, ComposeError> {
        let map = self.plan.map_page();
        let spec = self.spec;
        // The planner validated one placement per index, so the zip is exact.
        let physical = self
            .plan
            .index_placements()
            .zip(spec.indexes)
            .zip(self.plan.index_fields())
            .map(|(((root, row), index), fields)| PhysicalIndexSpec {
                fields,
                usage_map_page: map,
                usage_map_row: row,
                root,
                flags: index.kind.flags(),
                // EXP-0073: the prefix counts distinct keys, not leaf entries.
                entry_count: self
                    .initial_index
                    .as_ref()
                    .map_or(0, InitialLongIndex::distinct_count),
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
            .collect::<Result<Vec<_>, ComposeError>>()?;
        let long_value_maps = self
            .long_value
            .map(|column| LongValueMapSpec {
                column,
                owned: MapRowLocator::new(map, FIRST_INDEX_MAP_ROW),
                available: MapRowLocator::new(map, FIRST_INDEX_MAP_ROW + 1),
            })
            .into_iter()
            .collect::<Vec<_>>();
        encode_table_definition(
            &TableDefinitionSpec {
                kind: TableDefinitionKind::User,
                columns: spec.columns,
                system_column_classes: &[],
                physical_indexes: &physical,
                indexes: &logical,
                owned_map: MapRowLocator::new(map, OWNED_MAP_ROW),
                available_map: MapRowLocator::new(map, AVAILABLE_MAP_ROW),
                row_count: self.initial_row_count,
                long_value_maps: &long_value_maps,
            },
            output,
            budget,
        )
        .map_err(Into::into)
    }

    /// Builds the map page with the rows the definition names.
    pub(super) fn map_page(
        &self,
        foreign_index: Option<PageNumber>,
        budget: &mut ResourceBudget,
    ) -> Result<PageImage, ComposeError> {
        if foreign_index.is_some() && (!self.spec.indexes.is_empty() || self.long_value.is_some()) {
            return Err(ComposeError::UnobservedMapRowLayout);
        }
        let empty = inline_map_row(&[], budget)?;
        let mut owned = InlineUsageMapEncoder::new(
            PageNumber::new(0),
            ByteCount::new(MAP_BITMAP_BYTES),
            budget,
        )?;
        let mut available = InlineUsageMapEncoder::new(
            PageNumber::new(0),
            ByteCount::new(MAP_BITMAP_BYTES),
            budget,
        )?;
        let first = self.plan.definition_root().get()
            + self.plan.appended_page_count()
            + self
                .initial_long_values
                .as_ref()
                .map_or(0, InitialLongValues::page_count);
        for (offset, data) in self.initial_data.iter().enumerate() {
            let page = PageNumber::new(first + offset as u64);
            owned.set_page(page)?;
            if data.available {
                available.set_page(page)?;
            }
        }
        let mut owned_row = [0_u8; 133];
        let mut available_row = [0_u8; 133];
        owned.encode_into(&mut owned_row, budget)?;
        available.encode_into(&mut available_row, budget)?;
        let mut rows: Vec<[u8; 133]> = vec![owned_row, available_row];
        for (root, _) in self.plan.index_placements() {
            rows.push(inline_map_row(&[root.get()], budget)?);
        }
        if let Some(index) = foreign_index {
            rows.push(inline_map_row(&[index.get()], budget)?);
        }
        if self.long_value.is_some() {
            let maps = match &self.initial_long_values {
                Some(values) => values.maps(budget)?,
                None => [empty; 2],
            };
            rows.extend(maps);
        }
        let rows = rows.iter().map(<[u8; 133]>::as_slice).collect::<Vec<_>>();
        data_page(HEADER_PAGE, &rows, budget)
    }
}

/// Composes sequential table creates with their initial rows. EXP-0087 supplies
/// later-create page roles; EXP-0065 supplies row append placement. Combining
/// the existing per-table writers is a candidate construction.
pub(crate) fn compose_database_with_table_rows(
    requests: &[crate::TableRows<'_>],
    budget: &mut ResourceBudget,
) -> Result<WholeFileImagePlan, ComposeError> {
    if requests.len() > MAX_OBSERVED_TABLES {
        return Err(ComposeError::UnobservedTableCount {
            count: requests.len(),
            observed: MAX_OBSERVED_TABLES,
        });
    }
    budget.charge_allocation(ByteCount::new(
        (requests.len() * size_of::<PlannedCreate<'_>>()) as u64,
    ))?;
    let mut creates = Vec::new();
    creates
        .try_reserve_exact(requests.len())
        .map_err(|_| Error::Io {
            operation: "reserve initial table plans",
            kind: std::io::ErrorKind::OutOfMemory,
        })?;
    let mut next_page = EMPTY_DATABASE_PAGE_COUNT;
    for (position, request) in requests.iter().enumerate() {
        budget.charge_items(1)?;
        if let Some(first) = requests[..position]
            .iter()
            .position(|earlier| earlier.table.name.eq_ignore_ascii_case(request.table.name))
        {
            return Err(ComposeError::DuplicateTableName {
                first,
                second: position,
            });
        }
        let planned = PlannedCreate::new(&request.table, next_page, position == 0)?
            .with_rows(request.rows, budget)?;
        next_page = planned.page_count();
        creates.push(planned);
    }
    compose_planned_creates(&creates, budget)
}

/// Returns the ordinals of the columns that own long-value map groups.
fn long_value_columns<'s>(spec: &'s TableSpec<'_>) -> impl Iterator<Item = u16> + 's {
    spec.columns
        .iter()
        .enumerate()
        .filter(|(_, column)| column.column_type().is_long_value())
        .filter_map(|(ordinal, _)| u16::try_from(ordinal).ok())
}

#[cfg(test)]
#[path = "bootstrap_table_create_tests.rs"]
mod tests;
