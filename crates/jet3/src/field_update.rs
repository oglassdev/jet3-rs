//! Single-field row rewrites using EXP-0060/0061/0262 storage and EXP-0290 assignment semantics.
use crate::{
    ColumnPhysicalType, ColumnStorageClass, DatabaseReader, FieldUpdate, FileSource, PAGE_BYTES,
    ResourceBudget, RowColumnLayout, RowValue, TableDefinition, UpdateError,
};

pub(crate) fn plan(
    database: &mut DatabaseReader<FileSource>,
    definition: &TableDefinition,
    graph: crate::row_mutation_graph::RowGraph,
    request: FieldUpdate<'_>,
    check_relationships: bool,
    budget: &mut ResourceBudget,
) -> Result<crate::page_edits::PageEdits, UpdateError> {
    if check_relationships {
        crate::relationship_mutation::check(
            database,
            definition,
            request.table,
            crate::relationship_mutation::Change::Field(request.row, request.column, request.value),
            budget,
        )?;
    }
    plan_fields(
        database,
        definition,
        graph,
        request.row,
        &[(request.column, request.value)],
        budget,
    )
}

pub(crate) fn plan_fields(
    database: &mut DatabaseReader<FileSource>,
    definition: &TableDefinition,
    graph: crate::row_mutation_graph::RowGraph,
    selected_row: crate::RowLocator,
    assignments: &[(crate::ColumnOrdinal, RowValue<'_>)],
    budget: &mut ResourceBudget,
) -> Result<crate::page_edits::PageEdits, UpdateError> {
    let columns = definition.columns();
    if columns.len() > usize::from(u8::MAX) || assignments.len() > columns.len() {
        return Err(UpdateError::Unsupported("row column count"));
    }
    if graph.selected.len() > 2 {
        return Err(UpdateError::Unsupported(
            "mutation of multi-hop overflow chain",
        ));
    }
    let mut assigned = [false; u8::MAX as usize];
    let mut selected_columns = [crate::ColumnOrdinal::new(0); u8::MAX as usize];
    let options = crate::column_value_policy::options(database, definition, budget)?;
    crate::column_value_policy::refuse_rules(&options, definition)?;
    for (position, &(ordinal, value)) in assignments.iter().enumerate() {
        let index = usize::from(ordinal.get());
        let column = columns.get(index).ok_or(UpdateError::NotFound("column"))?;
        if column.auto_increment() {
            return Err(UpdateError::Unsupported("AutoIncrement column"));
        }
        if assigned[index] {
            return Err(UpdateError::Unsupported("duplicate field assignment"));
        }
        assigned[index] = true;
        selected_columns[position] = ordinal;
        crate::column_value_policy::check_value(
            ordinal.get(),
            column.physical_type(),
            column.storage(),
            options.columns[index],
            value,
        )?;
    }
    let selected_columns = &selected_columns[..assignments.len()];
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
    let mut long_values = crate::long_value_mutation::LongValues::load_fields(
        database,
        definition,
        selected_row,
        selected_columns,
        budget,
    )?;
    long_values.remove_selected(budget)?;
    let mut encoded = [0; PAGE_BYTES];
    let length = {
        let mut rows = database.rows(definition, budget)?;
        let mut length = None;
        while let Some(mut row) = rows.next_row()? {
            if row.locator() != selected_row {
                continue;
            }
            let mut values = [RowValue::Null; u8::MAX as usize];
            row.budget_mut().charge_items(columns.len() as u64)?;
            for column in columns {
                let ordinal = column.ordinal();
                values[usize::from(ordinal.get())] = if let Some((_, value)) = assignments
                    .iter()
                    .find(|(selected, _)| *selected == ordinal)
                {
                    *value
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
            let size = long_values.encode_fields_row(
                layout,
                &values[..columns.len()],
                selected_columns,
                &mut encoded,
                row.budget_mut(),
            )?;
            // Retain unassigned fixed bytes, including the padding of null fields.
            for column in columns {
                if assigned[usize::from(column.ordinal().get())]
                    || column.physical_type() == ColumnPhysicalType::Boolean
                {
                    continue;
                }
                if let Some(range) = row.stored_fixed_field_range(column.ordinal()) {
                    let start = range.start;
                    let end = range.end;
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
    let mut index = if definition.physical_indexes().iter().any(|index| {
        index
            .fields()
            .iter()
            .any(|field| selected_columns.contains(&field.column()))
    }) {
        Some(crate::index_mutation::load(database, definition, budget)?)
    } else {
        None
    };
    if let Some(index) = &mut index {
        index.replace_fields(database, definition, selected_row, assignments, budget)?;
    }
    let mut minimum = [0; PAGE_BYTES];
    let nulls = [RowValue::Null; u8::MAX as usize];
    let minimum_length =
        crate::encode_row(layout, &nulls[..columns.len()], &mut minimum, budget)?.get() as usize;
    let mut edits = crate::page_edits::PageEdits::new(database.geometry().page_count());
    long_values.stage(database, &mut edits, budget)?;
    crate::row_mutation_place::replace(
        database,
        definition,
        &graph.selected,
        &encoded[..length],
        &minimum[..minimum_length],
        &mut edits,
        budget,
    )?;
    if let Some(index) = index {
        index.stage(database, definition, &mut edits, budget)?;
    }
    Ok(edits)
}
