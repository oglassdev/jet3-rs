//! Scalar index mutations use EXP-0062/0126/0148/0150, with EXP-0230/0268 counters.
use crate::{
    DatabaseReader, FieldUpdate, FileSource, IndexNullPolicy, MapRowLocator, PAGE_BYTES, PageImage,
    PageNumber, PageOffset, ResourceBudget, RowLocator, RowValue, TableDefinition, WriteError,
    index::{
        entry::{EntryError, ScalarIndexEntry, ScalarIndexField, record_capacity},
        tree::builder::{ScalarIndexPages, TreeBuildError},
    },
    write::page_edits::{PageEdits, reserve},
};

pub(crate) use super::mutation_load::load;

pub(crate) struct Indexes {
    pub(super) indexes: Vec<MutableIndex>,
    pub(super) columns: [bool; u8::MAX as usize],
}

pub(super) struct MutableIndex {
    pub(super) ordinal: u16,
    pub(super) fields: Vec<ScalarIndexField>,
    pub(super) null_policy: IndexNullPolicy,
    pub(super) unique: bool,
    pub(super) entries: Vec<ScalarIndexEntry>,
    pub(super) mapped: Vec<PageNumber>,
    pub(super) changed: bool,
    pub(super) relationship_counter: bool,
    pub(super) counter: Option<CounterChange>,
}

pub(crate) fn entry_error(error: EntryError) -> WriteError {
    match error {
        EntryError::Encoding(error) => WriteError::Resource(error),
        EntryError::NullRequired => WriteError::Unsupported("null required index key"),
        EntryError::MissingColumn { .. } => WriteError::Mismatch("missing index key column"),
        EntryError::FieldCount { .. } => WriteError::Unsupported("numeric index field count"),
        EntryError::UnsupportedValue { .. } => WriteError::Unsupported("numeric index value"),
    }
}

pub(crate) fn tree_error(error: TreeBuildError) -> WriteError {
    match error {
        TreeBuildError::Encoding(error) => WriteError::Resource(error),
        TreeBuildError::NodeLimit { .. } => WriteError::Unsupported("numeric index node limit"),
        TreeBuildError::Layout(detail) => WriteError::Mismatch(detail),
    }
}

impl MutableIndex {
    pub(super) fn encode(
        &self,
        values: &[RowValue<'_>],
        row: RowLocator,
        budget: &mut ResourceBudget,
    ) -> Result<Option<ScalarIndexEntry>, WriteError> {
        ScalarIndexEntry::encode(&self.fields, values, self.null_policy, row, budget)
            .map_err(entry_error)
    }

    fn insert(
        &mut self,
        entry: ScalarIndexEntry,
        update_counter: bool,
        budget: &mut ResourceBudget,
    ) -> Result<(), WriteError> {
        budget.charge_work_units(
            self.entries.len() as u64
                * (2 * record_capacity(&self.fields) + size_of::<ScalarIndexEntry>()) as u64,
        )?;
        let first = self.entries.partition_point(|r| r.key() < entry.key());
        let present = self
            .entries
            .get(first)
            .is_some_and(|r| r.key() == entry.key());
        if self.unique && !entry.has_null() && present {
            return Err(WriteError::Unsupported("duplicate unique key"));
        }
        let position = self
            .entries
            .partition_point(|r| r.record() < entry.record());
        reserve(&mut self.entries, 1, budget)?;
        self.entries.insert(position, entry);
        self.changed = true;
        if update_counter && !present {
            self.counter = Some(CounterChange::Increment);
        }
        Ok(())
    }

    fn remove(&mut self, row: RowLocator, budget: &mut ResourceBudget) -> Result<(), WriteError> {
        budget.charge_work_units(
            self.entries.len() as u64
                * (2 * record_capacity(&self.fields) + size_of::<ScalarIndexEntry>()) as u64,
        )?;
        if let Some(position) = self.entries.iter().position(|r| r.locator() == row) {
            self.entries.remove(position);
            self.changed = true;
        } else if self.null_policy != IndexNullPolicy::IgnoreAllNull {
            return Err(WriteError::NotFound("indexed row"));
        }
        Ok(())
    }

    fn stage(
        &self,
        database: &mut DatabaseReader<FileSource>,
        table: &TableDefinition,
        edits: &mut PageEdits,
        budget: &mut ResourceBudget,
    ) -> Result<(), WriteError> {
        if self.changed {
            self.stage_tree(database, table, edits, budget)?;
        }
        if let Some(change) = self.counter {
            let mut before = [0; PAGE_BYTES];
            database.read_raw_page(table.root(), &mut before, budget)?;
            let mut after = PageImage::from_bytes(before);
            change_counter(&mut after, self.ordinal, change, budget)?;
            edits.set_image(database, table.root(), after, budget)?;
        }
        Ok(())
    }

    fn stage_tree(
        &self,
        database: &mut DatabaseReader<FileSource>,
        table: &TableDefinition,
        edits: &mut PageEdits,
        budget: &mut ResourceBudget,
    ) -> Result<(), WriteError> {
        let physical = &table.physical_indexes()[usize::from(self.ordinal)];
        let root = physical.root();
        let layout =
            ScalarIndexPages::new(&self.entries, usize::MAX, budget).map_err(tree_error)?;
        let mut pages = Vec::new();
        reserve(&mut pages, layout.len(), budget)?;
        pages.extend(
            self.mapped
                .iter()
                .copied()
                .filter(|p| *p != root)
                .take(layout.len() - 1),
        );
        while pages.len() < layout.len() - 1 {
            let page = edits.append(PageImage::from_bytes([0; PAGE_BYTES]), budget)?;
            let location = physical.usage_map();
            edits.map_bit(
                database,
                crate::alloc::mutation_map::global_locator(),
                page,
                true,
                false,
                budget,
            )?;
            edits.map_bit(
                database,
                MapRowLocator::new(location.page(), location.row()),
                page,
                false,
                true,
                budget,
            )?;
            pages.push(page);
        }
        pages.push(root);
        for (ordinal, page) in pages.iter().enumerate() {
            let mut before = [0; PAGE_BYTES];
            if page.get() < database.geometry().page_count() {
                database.read_raw_page(*page, &mut before, budget)?;
            }
            let image = layout
                .image(
                    ordinal,
                    &self.entries,
                    |n| pages.get(n).copied(),
                    table.root(),
                    &before,
                    budget,
                )
                .map_err(tree_error)?;
            edits.set_image(database, *page, image, budget)?;
        }
        Ok(())
    }
}

impl Indexes {
    pub(crate) fn insert(
        &mut self,
        values: &[RowValue<'_>],
        row: RowLocator,
        budget: &mut ResourceBudget,
    ) -> Result<(), WriteError> {
        for index in &mut self.indexes {
            if let Some(entry) = index.encode(values, row, budget)? {
                index.insert(entry, true, budget)?;
            }
        }
        Ok(())
    }

    pub(crate) fn remove(
        &mut self,
        row: RowLocator,
        budget: &mut ResourceBudget,
    ) -> Result<(), WriteError> {
        for index in &mut self.indexes {
            index.remove(row, budget)?;
            if index.relationship_counter {
                index.counter = Some(CounterChange::RemoveRelationshipEntry);
            }
        }
        Ok(())
    }

    pub(crate) fn replace(
        &mut self,
        row: RowLocator,
        values: &[RowValue<'_>],
        budget: &mut ResourceBudget,
    ) -> Result<(), WriteError> {
        self.replace_selected(row, values, None, budget)
    }

    fn replace_selected(
        &mut self,
        row: RowLocator,
        values: &[RowValue<'_>],
        columns: Option<&[crate::ColumnOrdinal]>,
        budget: &mut ResourceBudget,
    ) -> Result<(), WriteError> {
        for index in &mut self.indexes {
            let new = index.encode(values, row, budget)?;
            budget.charge_work_units(
                index.entries.len() as u64
                    * (2 * record_capacity(&index.fields) + size_of::<ScalarIndexEntry>()) as u64,
            )?;
            let old = index.entries.iter().find(|r| r.locator() == row);
            // EXP-0268/0286: equal relationship-key assignments also update retained state.
            if index.relationship_counter
                && columns.is_none_or(|columns| {
                    index.fields.iter().any(|field| {
                        columns
                            .iter()
                            .any(|column| field.column == usize::from(column.get()))
                    })
                })
            {
                index.counter = Some(CounterChange::RemoveRelationshipEntry);
            }
            if old == new.as_ref() {
                continue;
            }
            index.remove(row, budget)?;
            if let Some(entry) = new {
                index.insert(entry, false, budget)?;
            }
        }
        Ok(())
    }

    pub(crate) fn replace_field(
        &mut self,
        database: &mut DatabaseReader<FileSource>,
        table: &TableDefinition,
        request: FieldUpdate<'_>,
        budget: &mut ResourceBudget,
    ) -> Result<(), WriteError> {
        self.replace_fields(
            database,
            table,
            request.row,
            &[(request.column, request.value)],
            budget,
        )
    }

    pub(crate) fn replace_fields(
        &mut self,
        database: &mut DatabaseReader<FileSource>,
        table: &TableDefinition,
        selected: RowLocator,
        assignments: &[(crate::ColumnOrdinal, RowValue<'_>)],
        budget: &mut ResourceBudget,
    ) -> Result<(), WriteError> {
        let mut columns = [crate::ColumnOrdinal::new(0); u8::MAX as usize];
        if assignments.len() > columns.len() {
            return Err(WriteError::Unsupported("field assignment count"));
        }
        for (target, &(column, _)) in columns.iter_mut().zip(assignments) {
            *target = column;
        }
        budget.charge_items(u8::MAX as u64)?;
        let mut cursor = database.rows(table, budget)?;
        while let Some(mut row) = cursor.next_row()? {
            if row.locator() == selected {
                let mut values = crate::row::scalar_values::read(&mut row, &self.columns)?;
                for &(column, value) in assignments {
                    *values
                        .get_mut(usize::from(column.get()))
                        .ok_or(WriteError::NotFound("column"))? = value;
                }
                return self.replace_selected(
                    selected,
                    &values,
                    Some(&columns[..assignments.len()]),
                    row.budget_mut(),
                );
            }
        }
        Err(WriteError::NotFound("indexed row"))
    }

    pub(crate) fn stage(
        &self,
        database: &mut DatabaseReader<FileSource>,
        table: &TableDefinition,
        edits: &mut PageEdits,
        budget: &mut ResourceBudget,
    ) -> Result<(), WriteError> {
        for index in &self.indexes {
            index.stage(database, table, edits, budget)?;
        }
        Ok(())
    }
}

/// Plans the index changes of a single-field update; `None` when no index covers the field.
pub(crate) fn plan_field_update(
    database: &mut DatabaseReader<FileSource>,
    table: &TableDefinition,
    request: FieldUpdate<'_>,
    budget: &mut ResourceBudget,
) -> Result<Option<Indexes>, WriteError> {
    let mut indexed = false;
    for index in table.physical_indexes() {
        for key in index.fields() {
            budget.charge_items(1)?;
            indexed |= key.column() == request.column;
        }
    }
    if !indexed {
        return Ok(None);
    }
    let mut index = load(database, table, budget)?;
    index.replace_field(database, table, request, budget)?;
    Ok(Some(index))
}

// EXP-0059 prefixes, EXP-0230 ordinary counters, and EXP-0268/0286 relationship-key edits.
#[derive(Clone, Copy)]
pub(super) enum CounterChange {
    Increment,
    RemoveRelationshipEntry,
}

fn change_counter(
    image: &mut PageImage,
    ordinal: u16,
    change: CounterChange,
    budget: &mut ResourceBudget,
) -> Result<(), WriteError> {
    let offset = crate::definition::header::physical_prefix_offset(ordinal);
    let raw: [u8; 8] = image
        .as_bytes()
        .get(offset..offset + 8)
        .ok_or(WriteError::Mismatch("index counter offset"))?
        .try_into()
        .map_err(|_| WriteError::Mismatch("index counter width"))?;
    let mut first = u32::from_le_bytes([raw[0], raw[1], raw[2], raw[3]]);
    let mut second = u32::from_le_bytes([raw[4], raw[5], raw[6], raw[7]]);
    match change {
        CounterChange::Increment => {
            second = second
                .checked_add(1)
                .ok_or(WriteError::Unsupported("index counter overflow"))?
        }
        CounterChange::RemoveRelationshipEntry if first > 0 => {
            first -= 1;
            second = second.min(first);
        }
        CounterChange::RemoveRelationshipEntry => {}
    }
    let mut next = [0; 8];
    next[..4].copy_from_slice(&first.to_le_bytes());
    next[4..].copy_from_slice(&second.to_le_bytes());
    image.write_at(PageOffset::new(offset as u64), &next, budget)?;
    Ok(())
}
