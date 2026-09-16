//! Single-field row rewrites using EXP-0060/0061/0262 storage and EXP-0290 assignment semantics.
use std::{error::Error as StdError, path::Path};

use crate::{
    ColumnPhysicalType, ColumnStorageClass, DatabaseReader, FieldUpdate, FileSource, PAGE_BYTES,
    PublishStage, ResourceBudget, RowColumnLayout, RowValue, TableDefinition, UpdateError,
};

pub(crate) fn replace<H, HE>(
    path: &Path,
    mut database: DatabaseReader<FileSource>,
    definition: TableDefinition,
    graph: crate::row_mutation_graph::RowGraph,
    request: FieldUpdate<'_>,
    budget: &mut ResourceBudget,
    hook: H,
) -> Result<(), UpdateError>
where
    H: FnMut(PublishStage) -> Result<(), HE>,
    HE: StdError + Send + Sync + 'static,
{
    let columns = definition.columns();
    if columns.len() > usize::from(u8::MAX) {
        return Err(UpdateError::Unsupported("row column count"));
    }
    let selected = columns
        .get(usize::from(request.column.get()))
        .ok_or(UpdateError::NotFound("column"))?;
    let options = crate::column_value_policy::options(&mut database, &definition, budget)?;
    crate::column_value_policy::check_value(
        request.column.get(),
        selected.physical_type(),
        selected.storage(),
        options[usize::from(request.column.get())],
        request.value,
    )?;
    let mut layout = [RowColumnLayout::new(
        ColumnPhysicalType::Long,
        ColumnStorageClass::Fixed { offset: 0 },
        4,
    ); u8::MAX as usize];
    budget.charge_items(columns.len() as u64)?;
    for (ordinal, (target, column)) in layout.iter_mut().zip(columns).enumerate() {
        if usize::from(column.ordinal().get()) != ordinal {
            return Err(UpdateError::Unsupported("noncontiguous column ordinals"));
        }
        *target = column.into();
    }
    let layout = &layout[..columns.len()];
    let mut long_values = crate::long_value_mutation::LongValues::load_field(
        &mut database,
        &definition,
        request.row,
        request.column,
        budget,
    )?;
    long_values.remove_selected(budget)?;
    let mut encoded = [0; PAGE_BYTES];
    let length = {
        let mut rows = database.rows(&definition, budget)?;
        let mut length = None;
        while let Some(mut row) = rows.next_row()? {
            if row.locator() != request.row {
                continue;
            }
            let mut values = [RowValue::Null; u8::MAX as usize];
            row.budget_mut().charge_items(columns.len() as u64)?;
            for column in columns {
                let ordinal = column.ordinal();
                values[usize::from(ordinal.get())] = if ordinal == request.column {
                    request.value
                } else if matches!(
                    column.physical_type(),
                    ColumnPhysicalType::Memo | ColumnPhysicalType::LongBinary
                ) {
                    row.field(ordinal)
                        .ok_or(UpdateError::NotFound("long-value column"))?
                        .raw_bytes()
                        .map_or(RowValue::Null, RowValue::LongValue)
                } else {
                    crate::numeric_row_values::read_column(&mut row, ordinal)?
                };
            }
            let size = long_values.encode_field_row(
                layout,
                &values[..columns.len()],
                request.column,
                &mut encoded,
                row.budget_mut(),
            )?;
            // Retain unassigned fixed bytes, including the padding of null fields.
            for column in columns {
                if column.ordinal() == request.column
                    || column.physical_type() == ColumnPhysicalType::Boolean
                {
                    continue;
                }
                if let ColumnStorageClass::Fixed { offset } = column.storage() {
                    let start = 1 + usize::from(offset);
                    let end = start + usize::from(column.size());
                    let source = row
                        .raw_bytes()
                        .get(start..end)
                        .ok_or(UpdateError::Mismatch("fixed field source bounds"))?;
                    row.budget_mut().charge_work_units(source.len() as u64)?;
                    encoded
                        .get_mut(start..end)
                        .filter(|_| end <= size)
                        .ok_or(UpdateError::Mismatch("fixed field replacement bounds"))?
                        .copy_from_slice(source);
                }
            }
            length = Some(size);
            break;
        }
        length.ok_or(UpdateError::NotFound("row"))?
    };
    crate::relationship_mutation::check(
        &mut database,
        &definition,
        request.table,
        crate::relationship_mutation::Change::Field(request.row, request.column, request.value),
        budget,
    )?;
    let index = crate::update_index_key::plan(&mut database, &definition, request, budget)?;
    let mut minimum = [0; PAGE_BYTES];
    let nulls = [RowValue::Null; u8::MAX as usize];
    let minimum_length =
        crate::encode_row(layout, &nulls[..columns.len()], &mut minimum, budget)?.get() as usize;
    let mut edits = crate::page_edits::PageEdits::new(database.geometry().page_count());
    long_values.stage(&mut database, &mut edits, budget)?;
    crate::row_mutation_place::replace(
        &mut database,
        &definition,
        &graph.selected,
        &encoded[..length],
        &minimum[..minimum_length],
        &mut edits,
        budget,
    )?;
    if let Some(index) = index {
        index.stage(&mut database, &definition, &mut edits, budget)?;
    }
    edits.publish(path, database, budget, hook)
}
