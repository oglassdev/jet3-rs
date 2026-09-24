//! One private copy and one publication for every row in a cascade closure.
use super::cascade::*;
use crate::{
    ColumnOrdinal, DatabaseReader, FieldUpdate, FileSource, PublishStage, ResourceBudget,
    RowDelete, RowUpdate, RowValue, UpdateError, relationship::mutation::Change,
    write::page_edits::PageEdits,
};
use std::{cell::Cell, error::Error as StdError, fs::File, path::Path};

impl Plan<'_> {
    pub(crate) fn publish<H, HE>(
        self,
        path: &Path,
        database: DatabaseReader<FileSource>,
        budget: &mut ResourceBudget,
        hook: H,
    ) -> Result<(), UpdateError>
    where
        H: FnMut(PublishStage) -> Result<(), HE>,
        HE: StdError + Send + Sync + 'static,
    {
        let journal = Cell::new(Some(PageEdits::new(database.geometry().page_count())));
        let mut original = database.into_source();
        crate::write::atomic::atomic_update_budgeted(
            path,
            budget,
            |file, budget| -> Result<(), UpdateError> {
                let mut combined = journal
                    .take()
                    .ok_or(UpdateError::Mismatch("cascade journal absent"))?;
                self.stage(self.selected, file, &mut combined, budget)?;
                for (position, row) in self.rows.iter().enumerate() {
                    budget.charge_work_units(row.fields.len() as u64 + 1)?;
                    if position != self.selected
                        && (row.deleted || row.fields.iter().any(|f| f.after.is_some()))
                    {
                        self.stage(position, file, &mut combined, budget)?;
                    }
                }
                journal.set(Some(combined));
                Ok(())
            },
            |private, budget| -> Result<(), UpdateError> {
                let combined = journal
                    .take()
                    .ok_or(UpdateError::Mismatch("cascade journal absent"))?;
                let mut candidate = FileSource::open(private, budget.read_budget())?;
                combined.verify_private(&mut original, &mut candidate, budget)?;
                let mut candidate = DatabaseReader::from_source(candidate, budget)?;
                crate::relationship::catalog::validate(&mut candidate, budget)?;
                Ok(())
            },
            hook,
        )?;
        Ok(())
    }

    fn stage(
        &self,
        position: usize,
        file: &mut File,
        combined: &mut PageEdits,
        budget: &mut ResourceBudget,
    ) -> Result<(), UpdateError> {
        let row = &self.rows[position];
        let source = FileSource::from_file(file.try_clone()?, budget.read_budget())?;
        let mut database = DatabaseReader::from_source(source, budget)?;
        let definition = database.table_definition(row.table, budget)?;
        let edits = if row.deleted {
            crate::write::delete::plan(
                &mut database,
                &definition,
                RowDelete {
                    table: self.table,
                    row: row.locator,
                },
                false,
                budget,
            )?
        } else if position == self.selected
            && let Change::Replace(_, original) = self.request
        {
            let mut values = [RowValue::Null; u8::MAX as usize];
            if original.len() > values.len() {
                return Err(UpdateError::Unsupported("cascade replacement column count"));
            }
            values[..original.len()].copy_from_slice(original);
            for field in &row.fields {
                if let Some(after) = &field.after {
                    *values
                        .get_mut(usize::from(field.column.get()))
                        .ok_or(UpdateError::Mismatch("cascade replacement column"))? =
                        after.value();
                }
            }
            crate::write::row_update::plan(
                &mut database,
                &definition,
                RowUpdate {
                    table: self.table,
                    row: row.locator,
                    values: &values[..original.len()],
                },
                false,
                budget,
            )?
        } else {
            let mut assignments = [(ColumnOrdinal::new(0), RowValue::Null); u8::MAX as usize];
            let mut count = 0;
            if position == self.selected
                && let Change::Field(_, column, value) = self.request
            {
                assignments[0] = (column, value);
                count = 1;
            }
            for field in &row.fields {
                if let Some(after) = &field.after {
                    let value = after.value();
                    if count > 0 && assignments[0].0 == field.column {
                        assignments[0].1 = value;
                    } else {
                        *assignments
                            .get_mut(count)
                            .ok_or(UpdateError::Unsupported("cascade assignment count"))? =
                            (field.column, value);
                        count += 1;
                    }
                }
            }
            if count == 1 {
                crate::write::update::plan(
                    &mut database,
                    &definition,
                    FieldUpdate {
                        table: self.table,
                        row: row.locator,
                        column: assignments[0].0,
                        value: assignments[0].1,
                    },
                    false,
                    budget,
                )?
            } else {
                let graph = crate::row::mutation_graph::RowGraph::load(
                    &mut database,
                    &definition,
                    Some(row.locator),
                    budget,
                )?;
                crate::write::field_update::plan_fields(
                    &mut database,
                    &definition,
                    graph,
                    row.locator,
                    &assignments[..count],
                    budget,
                )?
            }
        };
        edits.apply_private(&mut database, file, combined, budget)
    }
}
