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
//! Definition chains use the linked 2,040-byte continuation payloads from
//! `EXP-0059`/`EXP-0105`. Consecutive placement before index roots extends the
//! compact `EXP-0107` construction as candidate allocation policy.
//!
//! `EXP-0091` supplies the null catalog `LvProp` form with a retained mapped
//! long-value page. The EXP-0208 Memo opt-in instead populates that page with
//! its named boolean properties. This composition remains a candidate until
//! separate DAO validation.
//!
//! Each Memo or LongBinary column takes one owned/available map-row pair
//! (`EXP-0077`). Pairs follow the table and index maps in column order, bounded
//! by the checked map-page builder. This combined placement is candidate policy.

use super::*;
use crate::creation::schema_plan::{
    AVAILABLE_MAP_ROW, FIRST_INDEX_MAP_ROW, OWNED_MAP_ROW, TableSchemaPlan, TableSpec,
    logical_index_order, plan_table_schema,
};

/// `EXP-0093`: `MSysObjects` `Flags` of a created user table.
const USER_FLAGS: i32 = 0;

/// A create with its pages assigned and its map-page rows laid out.
#[derive(Debug, Clone)]
pub(super) struct PlannedCreate<'a> {
    spec: &'a TableSpec<'a>,
    plan: TableSchemaPlan,
    long_value_count: usize,
    initial_data: Vec<InitialDataPage>,
    initial_long_values: Option<InitialLongValues>,
    initial_row_count: u32,
    initial_indexes: Vec<InitialLongIndex>,
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
        if spec.columns.iter().any(ColumnSpec::allow_zero_length)
            && (!first_create
                || spec.columns.len() != 2
                || !spec.indexes.is_empty()
                || spec.columns[0].column_type() != ColumnType::Long
                || spec.columns[0].name() != b"Id"
                || spec.columns[0].allow_zero_length()
                || spec.columns[1].column_type() != ColumnType::Memo
                || !spec.columns[1].allow_zero_length()
                || crate::memo_property::MemoProperty::new(spec.columns[1].name()).is_none())
        {
            return Err(ComposeError::UnsupportedMemoOption);
        }

        let long_value_count = long_value_columns(spec).count();
        Ok(Self {
            spec,
            plan,
            long_value_count,
            initial_data: Vec::new(),
            initial_long_values: None,
            initial_row_count: 0,
            initial_indexes: Vec::new(),
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
        let mut generated = InitialAutoIncrement::new(self.spec, rows, budget)?;
        self.initial_autoincrement = generated;
        self.initial_indexes = InitialLongIndex::for_table(self.spec, rows.len(), budget)?;
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
        if self.long_value_count != 0 {
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
            let row = if let Some(generated) = generated.as_mut() {
                generated.lower(row, ordinal, &mut lowered, budget)?;
                &lowered[..row.len()]
            } else {
                *row
            };
            let length = encode_initial_row(
                &layout,
                self.spec.columns.iter().any(ColumnSpec::allow_zero_length),
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
            let locator = crate::RowLocator::new(PageNumber::new(self.data_end()), slot);
            for index in &mut self.initial_indexes {
                index.push(row, locator, budget)?;
            }
        }
        for index in &mut self.initial_indexes {
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
        let page = PageNumber::new(self.data_end());
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

    pub(super) fn property_header(&self) -> Result<Option<[u8; 12]>, ComposeError> {
        self.memo_property()
            .map(|property| {
                let page = self
                    .plan
                    .property_page()
                    .ok_or(ComposeError::UnsupportedMemoOption)?;
                crate::long_value_writer::external_long_value_header(
                    property.len(),
                    crate::ExternalLongValueStorage::SinglePage,
                    crate::RowLocator::new(page, 0),
                )
                .map_err(|_| ComposeError::UnsupportedMemoOption)
            })
            .transpose()
    }

    pub(super) fn memo_property(&self) -> Option<crate::memo_property::MemoProperty<'a>> {
        self.spec
            .columns
            .iter()
            .find(|column| column.allow_zero_length())
            .and_then(|column| crate::memo_property::MemoProperty::new(column.name()))
    }

    /// Returns the page holding the catalog row's `LvProp` long value, present
    /// only on the database's first create.
    pub(super) fn property_page(&self) -> Option<u64> {
        self.plan.property_page().map(|page| page.get())
    }

    /// Returns the page count once every appended page is in place.
    pub(super) fn page_count(&self) -> u64 {
        self.data_end()
            + self
                .initial_indexes
                .iter()
                .map(InitialLongIndex::extra_page_count)
                .sum::<u64>()
    }

    // EXP-0093 gives each index its own root/map. Extra tree pages are grouped
    // by physical index after the shared data pages (EXP-0062 tree grammar).
    fn index_extra_start(&self, ordinal: usize) -> u64 {
        self.data_end()
            + self
                .initial_indexes
                .iter()
                .take(ordinal)
                .map(InitialLongIndex::extra_page_count)
                .sum::<u64>()
    }

    fn data_end(&self) -> u64 {
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
        self.initial_indexes
            .first()
            .map_or(0, InitialLongIndex::distinct_count)
    }

    /// Logical row locations in the same order used while packing initial data.
    pub(super) fn initial_row_locators(&self) -> impl Iterator<Item = crate::RowLocator> + '_ {
        let first = self.data_end() - self.initial_data.len() as u64;
        self.initial_data
            .iter()
            .enumerate()
            .flat_map(move |(page, data)| {
                (0..data.rows).map(move |slot| {
                    crate::RowLocator::new(PageNumber::new(first + page as u64), slot as u8)
                })
            })
    }

    pub(super) fn contains_initial_long(
        &self,
        value: i32,
        budget: &mut ResourceBudget,
    ) -> Result<bool, ComposeError> {
        self.initial_indexes
            .first()
            .ok_or(ComposeError::UnsupportedInitialIndexSchema)?
            .contains_single_long(value, budget)
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
    /// page, the first create's long-value page, definition continuations,
    /// then the index roots.
    pub(super) fn append_pages(
        &self,
        plan: &mut WholeFileImagePlan,
        append_map: &mut InlineUsageMapEncoder,
        budget: &mut ResourceBudget,
    ) -> Result<(), ComposeError> {
        // Check physical map capacity before encoding its row locators.
        let maps = self.map_page(None, budget)?;
        let definition = self.definition_pages(budget)?;
        let mut root = definition.root(self.plan.continuation_page(), budget)?;
        if let Some(generated) = self.initial_autoincrement {
            generated.write(&mut root, budget)?;
        }
        plan.append(PageImage::from_bytes(root), append_map, budget)?;
        plan.append(maps, append_map, budget)?;
        if self.plan.property_page().is_some() {
            let mut lval = DataPageBuilder::new_long_value(budget)?;
            if let Some(property) = self.memo_property() {
                let mut payload = [0; crate::memo_property::MAX_PAYLOAD];
                let length = property.encode(&mut payload, budget)?;
                lval.append_row(&payload[..length], budget)?;
            }
            plan.append(finish_data_builder(lval, budget)?, append_map, budget)?;
        }
        if let Some(first) = self.plan.continuation_page() {
            for (ordinal, payload) in definition.continuations().enumerate() {
                let image = definition.continuation(first, ordinal, payload, budget)?;
                plan.append(image, append_map, budget)?;
            }
        }
        let owner = self.plan.definition_root().get();
        for (ordinal, (root, _)) in self.plan.index_placements().enumerate() {
            let image = if let Some(index) = self.initial_indexes.get(ordinal) {
                index.image(
                    self.plan.definition_root(),
                    root,
                    self.index_extra_start(ordinal),
                    None,
                    budget,
                )?
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
        for (position, (index, (root, _))) in self
            .initial_indexes
            .iter()
            .zip(self.plan.index_placements())
            .enumerate()
        {
            for ordinal in 0..index.extra_page_count() {
                plan.append(
                    index.image(
                        self.plan.definition_root(),
                        root,
                        self.index_extra_start(position),
                        Some(ordinal as usize),
                        budget,
                    )?,
                    append_map,
                    budget,
                )?;
            }
        }
        Ok(())
    }

    fn definition_pages(
        &self,
        budget: &mut ResourceBudget,
    ) -> Result<definition_pages::DefinitionPages, ComposeError> {
        let mut pages = definition_pages::DefinitionPages::new(self.plan.definition_len(), budget)?;
        let length = self.encode_definition(pages.logical_mut(), budget)?.get() as usize;
        if length != self.plan.definition_len() {
            return Err(ComposeError::DefinitionLengthMismatch {
                planned: self.plan.definition_len(),
                encoded: length,
            });
        }
        Ok(pages)
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
            .enumerate()
            .map(
                |(ordinal, (((root, row), index), fields))| PhysicalIndexSpec {
                    fields,
                    usage_map_page: map,
                    usage_map_row: row,
                    root,
                    flags: index.kind.flags(),
                    // EXP-0073: the prefix counts distinct keys, not leaf entries.
                    entry_count: self
                        .initial_indexes
                        .get(ordinal)
                        .map_or(0, InitialLongIndex::distinct_count),
                },
            )
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
        budget.charge_allocation(ByteCount::new(
            (self.long_value_count * size_of::<LongValueMapSpec>()) as u64,
        ))?;
        let mut long_value_maps = Vec::new();
        long_value_maps
            .try_reserve_exact(self.long_value_count)
            .map_err(|_| Error::Io {
                operation: "reserve initial long-value map groups",
                kind: std::io::ErrorKind::OutOfMemory,
            })?;
        for (position, column) in long_value_columns(spec).enumerate() {
            let owned = usize::from(FIRST_INDEX_MAP_ROW) + spec.indexes.len() + 2 * position;
            let available = u8::try_from(owned + 1).map_err(|_| Error::IntegerConversion {
                value: (owned + 1) as u128,
                target: "u8 map row",
            })?;
            long_value_maps.push(LongValueMapSpec {
                column,
                owned: MapRowLocator::new(map, available - 1),
                available: MapRowLocator::new(map, available),
            });
        }
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
        foreign_index: Option<(PageNumber, u64)>,
        budget: &mut ResourceBudget,
    ) -> Result<PageImage, ComposeError> {
        if foreign_index.is_some() && (!self.spec.indexes.is_empty() || self.long_value_count != 0)
        {
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
        let mut builder = DataPageBuilder::new(PageNumber::new(HEADER_PAGE), budget)?;
        builder.append_row(&owned_row, budget)?;
        builder.append_row(&available_row, budget)?;
        for (ordinal, (root, _)) in self.plan.index_placements().enumerate() {
            let row = initial_index_map(
                root,
                self.index_extra_start(ordinal),
                self.initial_indexes
                    .get(ordinal)
                    .map_or(0, InitialLongIndex::extra_page_count),
                budget,
            )?;
            builder.append_row(&row, budget)?;
        }
        if let Some((root, extra)) = foreign_index {
            builder.append_row(
                &initial_index_map(root, root.get() + 1, extra, budget)?,
                budget,
            )?;
        }
        for column in long_value_columns(self.spec) {
            let maps = match &self.initial_long_values {
                Some(values) => values.maps(column, budget)?,
                None => [empty; 2],
            };
            for row in maps {
                builder.append_row(&row, budget)?;
            }
        }
        finish_data_builder(builder, budget)
    }
}

/// Composes sequential table creates with their initial rows. EXP-0087 supplies
/// later-create page roles; EXP-0065 supplies row append placement. Combining
/// the existing per-table writers is a candidate construction.
pub(crate) fn compose_database_with_table_rows(
    requests: &[crate::TableRows<'_>],
    budget: &mut ResourceBudget,
) -> Result<WholeFileImagePlan, ComposeError> {
    let mut creates = reserve_creates(requests.len(), budget)?;
    let mut next_page = EMPTY_DATABASE_PAGE_COUNT;
    for (position, request) in requests.iter().enumerate() {
        budget.charge_items(1)?;
        budget.charge_work_units(position as u64)?;
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

/// Bounds the EXP-0087/0222 counter without extrapolating its overflow.
// EXP-0249: close-empty/reopen-per-create history advances one 16-bit slot.
pub(super) fn creation_counter(count: usize) -> Result<u16, ComposeError> {
    u16::try_from(count)
        .ok()
        .and_then(|count| count.checked_mul(2))
        .and_then(|count| count.checked_add(0x0100))
        .ok_or(ComposeError::TableCountOverflow {
            count,
            maximum: usize::from((u16::MAX - 0x0100) / 2),
        })
}

pub(super) fn reserve_creates<'a>(
    count: usize,
    budget: &mut ResourceBudget,
) -> Result<Vec<PlannedCreate<'a>>, ComposeError> {
    creation_counter(count)?;
    let allocation = (count as u64)
        .checked_mul(size_of::<PlannedCreate<'_>>() as u64)
        .ok_or(Error::Arithmetic {
            operation: "size initial table plans",
        })?;
    budget.charge_allocation(ByteCount::new(allocation))?;
    let mut creates = Vec::new();
    creates.try_reserve_exact(count).map_err(|_| Error::Io {
        operation: "reserve initial table plans",
        kind: std::io::ErrorKind::OutOfMemory,
    })?;
    Ok(creates)
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
#[path = "table_create_tests.rs"]
mod tests;

fn initial_index_map(
    root: PageNumber,
    first_extra: u64,
    extra_count: u64,
    budget: &mut ResourceBudget,
) -> Result<[u8; 133], ComposeError> {
    let mut map =
        InlineUsageMapEncoder::new(PageNumber::new(0), ByteCount::new(MAP_BITMAP_BYTES), budget)?;
    map.set_page(root)?;
    for page in first_extra..first_extra + extra_count {
        map.set_page(PageNumber::new(page))?;
    }
    let mut row = [0_u8; 133];
    map.encode_into(&mut row, budget)?;
    Ok(row)
}

#[path = "definition_pages.rs"]
mod definition_pages;

#[cfg(test)]
#[path = "definition_chain_tests.rs"]
mod definition_chain_tests;
