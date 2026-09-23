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
//! long-value page. EXP-0208/0266 explicit Text/Memo options populate named
//! boolean properties on any table, using single or chained LVAL pages.
//!
//! Each Memo or LongBinary column takes one owned/available map-row pair
//! (`EXP-0077`). Pairs follow the table and index maps in column order, bounded
//! by checked map-page builders. EXP-0252 supplies independent page/slot
//! locators; consecutive packed pages are a candidate construction policy.

use super::*;
use crate::creation::schema_plan::{
    AVAILABLE_MAP_ROW, FIRST_INDEX_MAP_ROW, OWNED_MAP_ROW, TableSchemaPlan, TableSpec,
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
    relationships: Vec<LogicalIndexSpec<'a>>,
    declared_indexes: usize,
    properties: Option<crate::column_properties::ColumnProperties>,
}

#[derive(Debug, Clone)]
struct InitialDataPage {
    image: PageImage,
    available: bool,
}

impl<'a> PlannedCreate<'a> {
    /// Plans `spec` at `first_page`, including explicit catalog property pages.
    pub(super) fn new(
        spec: &'a TableSpec<'a>,
        first_page: u64,
        first_create: bool,
        budget: &mut ResourceBudget,
    ) -> Result<Self, ComposeError> {
        Self::new_with_relationships(
            spec,
            first_page,
            first_create,
            &[],
            spec.indexes.len(),
            budget,
        )
    }

    pub(super) fn new_with_relationship(
        spec: &'a TableSpec<'a>,
        first_page: u64,
        first_create: bool,
        relationship: Option<LogicalIndexSpec<'a>>,
        budget: &mut ResourceBudget,
    ) -> Result<Self, ComposeError> {
        Self::new_with_relationships(
            spec,
            first_page,
            first_create,
            relationship.as_slice(),
            spec.indexes.len(),
            budget,
        )
    }

    pub(super) fn new_with_relationships(
        spec: &'a TableSpec<'a>,
        first_page: u64,
        first_create: bool,
        relations: &[LogicalIndexSpec<'a>],
        declared_indexes: usize,
        budget: &mut ResourceBudget,
    ) -> Result<Self, ComposeError> {
        let mut replaced = [false; crate::creation::schema_plan::MAX_OBSERVED_INDEXES];
        let mut names = Vec::new();
        crate::resource::reserve(&mut names, relations.len(), budget)?;
        budget.charge_work_units(relations.len() as u64)?;
        for index in relations {
            let physical = spec.indexes.get(usize::from(index.physical_index)).ok_or(
                ComposeError::UnsupportedRelationship {
                    detail: "relationship physical index missing",
                },
            )?;
            if !matches!(index.kind, LogicalIndexKindSpec::Relationship { .. }) {
                return Err(ComposeError::UnsupportedRelationship {
                    detail: "expected relationship record",
                });
            }
            let slot = replaced.get_mut(usize::from(index.physical_index)).ok_or(
                ComposeError::UnsupportedRelationship {
                    detail: "relationship physical index missing",
                },
            )?;
            // EXP-0279/0286: generated trees replace their placeholder logical alias.
            let generated = usize::from(index.physical_index) >= declared_indexes
                || (matches!(
                    index.kind,
                    LogicalIndexKindSpec::Relationship {
                        side: crate::RelationshipSide::ForeignTable,
                        ..
                    }
                ) && index.name == physical.name);
            let append = *slot || !generated;
            *slot = true;
            if append {
                names.push(index.name);
            }
        }
        let plan = crate::creation::schema_plan::plan_table_schema_with_generated_indexes(
            spec,
            first_page,
            first_create,
            &names,
            declared_indexes,
            budget,
        )?;
        if spec.columns.iter().any(|column| {
            column.allow_zero_length()
                && !crate::column_properties::has_zero_length_property(column.physical_type())
        }) {
            return Err(ComposeError::UnsupportedMemoOption);
        }
        let mut relationships = Vec::new();
        crate::resource::reserve(&mut relationships, relations.len(), budget)?;
        relationships.extend_from_slice(relations);
        let long_value_count = long_value_columns(spec).count();
        let properties =
            crate::column_properties::ColumnProperties::new(spec.columns, spec.validation, budget)
                .map_err(ComposeError::Properties)?;
        Ok(Self {
            spec,
            plan,
            long_value_count,
            initial_data: Vec::new(),
            initial_long_values: None,
            initial_row_count: 0,
            initial_indexes: Vec::new(),
            initial_autoincrement: None,
            relationships,
            declared_indexes,
            properties,
        })
    }

    pub(super) fn schema(&self) -> &TableSchemaPlan {
        &self.plan
    }

    pub(super) fn set_relationship_target(
        &mut self,
        target: PageNumber,
    ) -> Result<(), ComposeError> {
        match self.relationships.first_mut().map(|index| &mut index.kind) {
            Some(LogicalIndexKindSpec::Relationship { related_table, .. }) => {
                *related_table = target;
                Ok(())
            }
            _ => Err(ComposeError::UnsupportedRelationship {
                detail: "missing planned relationship",
            }),
        }
    }

    pub(super) fn resolve_relationship_targets(
        &mut self,
        roots: &[PageNumber],
    ) -> Result<(), ComposeError> {
        for index in &mut self.relationships {
            if let LogicalIndexKindSpec::Relationship { related_table, .. } = &mut index.kind {
                let position = usize::try_from(related_table.get()).map_err(|_| {
                    ComposeError::UnsupportedRelationship {
                        detail: "relationship table ordinal",
                    }
                })?;
                *related_table =
                    *roots
                        .get(position)
                        .ok_or(ComposeError::UnsupportedRelationship {
                            detail: "relationship table ordinal",
                        })?;
            }
        }
        Ok(())
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
        // EXP-0299 expressions are not evaluated, so rows cannot be checked against them.
        if self.spec.validation.rule.is_some()
            || self
                .spec
                .columns
                .iter()
                .any(|column| column.validation_rule().is_some())
        {
            return Err(ComposeError::ValidationRuleRows);
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
                self.spec.columns,
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
        allocation_maps::check_page(page.get())?;
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
        self.initial_data.push(InitialDataPage {
            image: finish_data_builder(builder, budget)?,
            available,
        });
        Ok(())
    }

    pub(super) fn property_header(&self) -> Result<Option<[u8; 12]>, ComposeError> {
        self.column_properties()
            .map(|property| {
                let page = self
                    .plan
                    .property_page()
                    .ok_or(ComposeError::UnsupportedMemoOption)?;
                crate::long_value_writer::external_long_value_header(
                    property.len(),
                    if self.plan.property_page_count() == 1 {
                        crate::ExternalLongValueStorage::SinglePage
                    } else {
                        crate::ExternalLongValueStorage::Chained
                    },
                    crate::RowLocator::new(page, 0),
                )
                .map_err(|_| ComposeError::UnsupportedMemoOption)
            })
            .transpose()
    }

    fn column_properties(&self) -> Option<&crate::column_properties::ColumnProperties> {
        self.properties.as_ref()
    }

    pub(super) fn property_pages(&self, available: bool) -> impl Iterator<Item = u64> + Clone {
        self.plan
            .property_page()
            .filter(|_| !available || self.plan.property_page_count() == 1)
            .into_iter()
            .flat_map(|page| page.get()..page.get() + self.plan.property_page_count() as u64)
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

    pub(super) fn contains_initial_key(
        &self,
        physical: u16,
        kinds: &[crate::numeric_index_key::NumericKeyType],
        values: &[RowValue<'_>],
        budget: &mut ResourceBudget,
    ) -> Result<bool, ComposeError> {
        self.initial_indexes
            .get(usize::from(physical))
            .ok_or(ComposeError::UnsupportedInitialIndexSchema)?
            .contains_key(kinds, values, budget)
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
    /// pages, catalog property pages, definition continuations,
    /// then the index roots.
    pub(super) fn append_pages(
        &self,
        plan: &mut WholeFileImagePlan,
        maps: &mut AllocationMaps,
        budget: &mut ResourceBudget,
    ) -> Result<(), ComposeError> {
        let definition = self.definition_pages(budget)?;
        let mut root = definition.root(self.plan.continuation_page(), budget)?;
        if let Some(generated) = self.initial_autoincrement {
            generated.write(&mut root, budget)?;
        }
        plan.append_image(PageImage::from_bytes(root), budget)?;
        for ordinal in 0..self.plan.map_page_count() {
            plan.append_image(self.map_page_at(ordinal, maps, budget)?, budget)?;
        }
        self.append_property_pages(plan, budget)?;
        if let Some(first) = self.plan.continuation_page() {
            for (ordinal, payload) in definition.continuations().enumerate() {
                let image = definition.continuation(first, ordinal, payload, budget)?;
                plan.append_image(image, budget)?;
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
            plan.append_image(image, budget)?;
        }
        if let Some(values) = &self.initial_long_values {
            values.append_pages(plan, budget)?;
        }
        for data in &self.initial_data {
            plan.append_image(data.image.clone(), budget)?;
        }
        for (position, (index, (root, _))) in self
            .initial_indexes
            .iter()
            .zip(self.plan.index_placements())
            .enumerate()
        {
            for ordinal in 0..index.extra_page_count() {
                plan.append_image(
                    index.image(
                        self.plan.definition_root(),
                        root,
                        self.index_extra_start(position),
                        Some(ordinal as usize),
                        budget,
                    )?,
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
                    usage_map_page: self.plan.map_location(usize::from(row)).page(),
                    usage_map_row: self.plan.map_location(usize::from(row)).row(),
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
        let mut logical = spec
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
                    kind: index.kind.logical_kind(),
                })
            })
            .collect::<Result<Vec<_>, ComposeError>>()?;
        let mut replaced = [false; crate::creation::schema_plan::MAX_OBSERVED_INDEXES];
        budget.charge_work_units(
            (self.relationships.len() as u64).saturating_mul(logical.len() as u64 + 1),
        )?;
        for &index in &self.relationships {
            let foreign = matches!(
                index.kind,
                LogicalIndexKindSpec::Relationship {
                    side: crate::RelationshipSide::ForeignTable,
                    ..
                }
            );
            let flag = replaced.get_mut(usize::from(index.physical_index)).ok_or(
                ComposeError::UnsupportedRelationship {
                    detail: "relationship physical index missing",
                },
            )?;
            let generated = usize::from(index.physical_index) >= self.declared_indexes
                || (foreign
                    && spec
                        .indexes
                        .get(usize::from(index.physical_index))
                        .is_some_and(|physical| physical.name == index.name));
            if !*flag && generated {
                let slot = logical
                    .iter_mut()
                    .find(|existing| existing.physical_index == index.physical_index)
                    .ok_or(ComposeError::UnsupportedRelationship {
                        detail: "foreign physical index missing",
                    })?;
                *slot = index;
                *flag = true;
            } else {
                crate::resource::reserve(&mut logical, 1, budget)?;
                logical.push(index);
            }
        }
        sort_logical_indexes(&mut logical, budget)?;
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
            long_value_maps.push(LongValueMapSpec {
                column,
                owned: self.plan.map_location(owned),
                available: self.plan.map_location(owned + 1),
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

    fn map_page_at(
        &self,
        ordinal: usize,
        maps: &mut AllocationMaps,
        budget: &mut ResourceBudget,
    ) -> Result<PageImage, ComposeError> {
        use crate::creation::schema_plan::MAP_ROWS_PER_PAGE;
        if ordinal >= self.plan.map_page_count() {
            return Err(ComposeError::UnobservedMapRowLayout);
        }
        let count = 2 + self.spec.indexes.len() + 2 * self.long_value_count;
        let start = ordinal * MAP_ROWS_PER_PAGE;
        let mut builder = DataPageBuilder::new(PageNumber::new(HEADER_PAGE), budget)?;
        for row in start..count.min(start + MAP_ROWS_PER_PAGE) {
            let bytes = if row < 2 {
                self.table_map_row(row == 1, maps, budget)?
            } else if row - 2 < self.spec.indexes.len() {
                let index = row - 2;
                let (root, _) = self
                    .plan
                    .index_placements()
                    .nth(index)
                    .ok_or(ComposeError::UnobservedMapRowLayout)?;
                let extra = self
                    .initial_indexes
                    .get(index)
                    .map_or(0, InitialLongIndex::extra_page_count);
                maps.row(
                    std::iter::once(root.get()).chain(
                        self.index_extra_start(index)..self.index_extra_start(index) + extra,
                    ),
                    budget,
                )?
            } else {
                let offset = row - 2 - self.spec.indexes.len();
                let column = long_value_columns(self.spec)
                    .nth(offset / 2)
                    .ok_or(ComposeError::UnobservedMapRowLayout)?;
                match &self.initial_long_values {
                    Some(values) => values.map(column, !offset.is_multiple_of(2), maps, budget)?,
                    None => inline_map_row(&[], budget)?,
                }
            };
            builder.append_row(&bytes, budget)?;
        }
        finish_data_builder(builder, budget)
    }

    fn table_map_row(
        &self,
        available: bool,
        maps: &mut AllocationMaps,
        budget: &mut ResourceBudget,
    ) -> Result<[u8; 133], ComposeError> {
        let first = self.data_end() - self.initial_data.len() as u64;
        maps.row(
            self.initial_data
                .iter()
                .enumerate()
                .filter_map(|(offset, data)| {
                    (!available || data.available).then_some(first + offset as u64)
                }),
            budget,
        )
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
        budget.charge_work_units((position as u64).saturating_mul(512))?;
        if let Some(first) = requests[..position]
            .iter()
            .position(|earlier| catalog_names_equal(earlier.table.name, request.table.name))
        {
            return Err(ComposeError::DuplicateTableName {
                first,
                second: position,
            });
        }
        let planned = PlannedCreate::new(&request.table, next_page, position == 0, budget)?
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

#[path = "definition_pages.rs"]
mod definition_pages;

#[cfg(test)]
#[path = "definition_chain_tests.rs"]
mod definition_chain_tests;

#[path = "column_property_pages.rs"]
mod column_property_pages;

/// EXP-0277: logical ordinals follow the English-US/CP1252 name collation.
fn sort_logical_indexes(
    indexes: &mut [LogicalIndexSpec<'_>],
    budget: &mut ResourceBudget,
) -> Result<(), ComposeError> {
    use crate::catalog_name_key::NameKey;
    let mut keyed = Vec::new();
    crate::resource::reserve(&mut keyed, indexes.len(), budget)?;
    budget.charge_work_units((indexes.len() as u64).saturating_mul(512))?;
    for &index in indexes.iter() {
        keyed.push((NameKey::new(index.name)?, index));
    }
    budget.charge_work_units(
        (indexes.len() as u64).saturating_mul(u64::from(indexes.len().max(1).ilog2()) + 1) * 194,
    )?;
    keyed.sort_unstable_by(|(left, _), (right, _)| left.bytes().cmp(right.bytes()));
    for (slot, (_, index)) in indexes.iter_mut().zip(keyed) {
        *slot = index;
    }
    Ok(())
}
