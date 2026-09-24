//! Scalar index mutations use EXP-0062/0126/0148/0150, with EXP-0230/0268 counters.
use crate::{
    DatabaseReader, FieldUpdate, FileSource, IndexNullPolicy, MapRowLocator, PAGE_BYTES, PageImage,
    PageNumber, ResourceBudget, RowLocator, RowValue, TableDefinition, UpdateError,
    index::{
        counter::Change,
        entry::{EntryError, NumericIndexEntry, NumericIndexField, record_capacity},
        tree::builder::{NumericIndexPages, TreeBuildError},
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
    pub(super) fields: Vec<NumericIndexField>,
    pub(super) null_policy: IndexNullPolicy,
    pub(super) unique: bool,
    pub(super) entries: Vec<NumericIndexEntry>,
    pub(super) mapped: Vec<PageNumber>,
    pub(super) changed: bool,
    pub(super) relationship_counter: bool,
    pub(super) counter: Option<Change>,
}

pub(crate) fn entry_error(error: EntryError) -> UpdateError {
    match error {
        EntryError::Encoding(error) => UpdateError::Resource(error),
        EntryError::NullRequired => UpdateError::Unsupported("null required index key"),
        EntryError::MissingColumn { .. } => UpdateError::Mismatch("missing index key column"),
        EntryError::FieldCount { .. } => UpdateError::Unsupported("numeric index field count"),
        EntryError::UnsupportedValue { .. } => UpdateError::Unsupported("numeric index value"),
    }
}

pub(crate) fn tree_error(error: TreeBuildError) -> UpdateError {
    match error {
        TreeBuildError::Encoding(error) => UpdateError::Resource(error),
        TreeBuildError::NodeLimit { .. } => UpdateError::Unsupported("numeric index node limit"),
        TreeBuildError::Layout(detail) => UpdateError::Mismatch(detail),
    }
}

impl MutableIndex {
    pub(super) fn encode(
        &self,
        values: &[RowValue<'_>],
        row: RowLocator,
        budget: &mut ResourceBudget,
    ) -> Result<Option<NumericIndexEntry>, UpdateError> {
        NumericIndexEntry::encode(&self.fields, values, self.null_policy, row, budget)
            .map_err(entry_error)
    }

    fn insert(
        &mut self,
        entry: NumericIndexEntry,
        update_counter: bool,
        budget: &mut ResourceBudget,
    ) -> Result<(), UpdateError> {
        budget.charge_work_units(
            self.entries.len() as u64
                * (2 * record_capacity(&self.fields) + size_of::<NumericIndexEntry>()) as u64,
        )?;
        let first = self.entries.partition_point(|r| r.key() < entry.key());
        let present = self
            .entries
            .get(first)
            .is_some_and(|r| r.key() == entry.key());
        if self.unique && !entry.has_null() && present {
            return Err(UpdateError::Unsupported("duplicate unique key"));
        }
        let position = self
            .entries
            .partition_point(|r| r.record() < entry.record());
        reserve(&mut self.entries, 1, budget)?;
        self.entries.insert(position, entry);
        self.changed = true;
        if update_counter && !present {
            self.counter = Some(Change::Increment);
        }
        Ok(())
    }

    fn remove(&mut self, row: RowLocator, budget: &mut ResourceBudget) -> Result<(), UpdateError> {
        budget.charge_work_units(
            self.entries.len() as u64
                * (2 * record_capacity(&self.fields) + size_of::<NumericIndexEntry>()) as u64,
        )?;
        if let Some(position) = self.entries.iter().position(|r| r.locator() == row) {
            self.entries.remove(position);
            self.changed = true;
        } else if self.null_policy != IndexNullPolicy::IgnoreAllNull {
            return Err(UpdateError::NotFound("indexed row"));
        }
        Ok(())
    }

    fn stage(
        &self,
        database: &mut DatabaseReader<FileSource>,
        table: &TableDefinition,
        edits: &mut PageEdits,
        budget: &mut ResourceBudget,
    ) -> Result<(), UpdateError> {
        if self.changed {
            self.stage_tree(database, table, edits, budget)?;
        }
        if let Some(change) = self.counter {
            let mut before = [0; PAGE_BYTES];
            database.read_raw_page(table.root(), &mut before, budget)?;
            let mut after = PageImage::from_bytes(before);
            crate::index::counter::change(&mut after, self.ordinal, change, budget)?;
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
    ) -> Result<(), UpdateError> {
        let physical = &table.physical_indexes()[usize::from(self.ordinal)];
        let root = physical.root();
        let layout =
            NumericIndexPages::new(&self.entries, usize::MAX, budget).map_err(tree_error)?;
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
                MapRowLocator::new(PageNumber::new(1), 0),
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
    ) -> Result<(), UpdateError> {
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
    ) -> Result<(), UpdateError> {
        for index in &mut self.indexes {
            index.remove(row, budget)?;
            if index.relationship_counter {
                index.counter = Some(Change::RemoveRelationshipEntry);
            }
        }
        Ok(())
    }

    pub(crate) fn replace(
        &mut self,
        row: RowLocator,
        values: &[RowValue<'_>],
        budget: &mut ResourceBudget,
    ) -> Result<(), UpdateError> {
        self.replace_selected(row, values, None, budget)
    }

    fn replace_selected(
        &mut self,
        row: RowLocator,
        values: &[RowValue<'_>],
        columns: Option<&[crate::ColumnOrdinal]>,
        budget: &mut ResourceBudget,
    ) -> Result<(), UpdateError> {
        for index in &mut self.indexes {
            let new = index.encode(values, row, budget)?;
            budget.charge_work_units(
                index.entries.len() as u64
                    * (2 * record_capacity(&index.fields) + size_of::<NumericIndexEntry>()) as u64,
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
                index.counter = Some(Change::RemoveRelationshipEntry);
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
    ) -> Result<(), UpdateError> {
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
    ) -> Result<(), UpdateError> {
        let mut columns = [crate::ColumnOrdinal::new(0); u8::MAX as usize];
        if assignments.len() > columns.len() {
            return Err(UpdateError::Unsupported("field assignment count"));
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
                        .ok_or(UpdateError::NotFound("column"))? = value;
                }
                return self.replace_selected(
                    selected,
                    &values,
                    Some(&columns[..assignments.len()]),
                    row.budget_mut(),
                );
            }
        }
        Err(UpdateError::NotFound("indexed row"))
    }

    pub(crate) fn stage(
        &self,
        database: &mut DatabaseReader<FileSource>,
        table: &TableDefinition,
        edits: &mut PageEdits,
        budget: &mut ResourceBudget,
    ) -> Result<(), UpdateError> {
        for index in &self.indexes {
            index.stage(database, table, edits, budget)?;
        }
        Ok(())
    }
}
