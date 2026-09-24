//! Appended columns use EXP-0059 records, EXP-0077 maps and EXP-0257/0297 old rows.
use crate::{
    BinaryWriter, ColumnDefinition, ColumnPhysicalType, ColumnSpec, ColumnStorageClass, ColumnType,
    ResourceBudget, RowValue, TableDefinitionKind, TableSpec, UpdateError,
    definition::header::{
        COLUMN_COUNT, DEFINITION_HEADER_LEN, STORAGE_COLUMN_COUNT, STORAGE_VARIABLE_COUNT,
    },
    write::page_edits::{PageEdits, reserve},
};
use std::fs::File;

pub(crate) fn create(
    file: &mut File,
    journal: &mut PageEdits,
    table: &[u8],
    column: ColumnSpec<'_>,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    let (catalog_root, row, properties, root, ordinal, auto_record) =
        crate::schema::edit::apply(file, journal, budget, |database, budget| {
            let order = database.header().sort_order();
            let definition = crate::write::update::indexed_writable_table(database, table, budget)?;
            crate::schema::edit::name(order, column.name(), 64)?;
            crate::schema::edit::distinct(
                order,
                column.name(),
                definition.columns().iter().map(|c| c.name().raw_bytes()),
                budget,
            )?;
            if column.allow_zero_length()
                && !crate::properties::column::has_zero_length_property(column.physical_type())
            {
                return Err(UpdateError::Unsupported("AllowZeroLength column type"));
            }
            if column.column_type() == ColumnType::AutoIncrement
                && definition
                    .columns()
                    .iter()
                    .any(ColumnDefinition::auto_increment)
            {
                return Err(UpdateError::Unsupported("multiple AutoIncrement columns"));
            }
            let mut columns = Vec::new();
            reserve(&mut columns, definition.columns().len() + 1, budget)?;
            for existing in definition.columns() {
                columns.push(spec(existing)?);
            }
            columns.push(column);
            crate::create::schema_plan::plan_table_schema_for_order(
                &TableSpec {
                    validation: crate::TableValidation::NONE,
                    name: table,
                    columns: &columns,
                    indexes: &[],
                },
                database.geometry().page_count(),
                false,
                &[],
                0,
                order,
                budget,
            )?;
            let (catalog, row, properties) =
                crate::schema::properties::load(database, &definition, budget)?;
            let properties = crate::schema::properties::add(&properties, column, budget)?;
            let mut edited = crate::schema::definition::DefinitionEdit::new(&definition, budget)?;
            let mut fixed = fixed_offset(&definition, column, budget)?;
            let mut variables = u16::from_le_bytes([
                edited.header[STORAGE_VARIABLE_COUNT],
                edited.header[STORAGE_VARIABLE_COUNT + 1],
            ]);
            let ordinal = definition.storage_column_count();
            if ordinal >= 255 || variables >= 255 {
                return Err(UpdateError::Unsupported("column storage capacity"));
            }
            let live_count = definition.columns().len() as u16 + 1;
            let fixed_before = fixed;
            let resolved = crate::definition::column_writer::resolve_column(
                ordinal,
                &column,
                TableDefinitionKind::User,
                None,
                &mut fixed,
                &mut variables,
            )?;
            let mut record = [0; 18];
            crate::definition::column_writer::write_column_record_with_context(
                &mut BinaryWriter::new(&mut record, budget)?,
                ordinal,
                &column,
                resolved,
                TableDefinitionKind::User,
                order
                    .encoding_context()
                    .ok_or(UpdateError::Unsupported("column encoding context"))?,
            )?;
            let auto_record = (column.column_type() == ColumnType::AutoIncrement).then_some(record);
            if auto_record.is_some() {
                let plain = ColumnSpec::new(column.name(), ColumnType::Long);
                let mut offset = fixed_before;
                let mut counter = variables;
                let resolved = crate::definition::column_writer::resolve_column(
                    ordinal,
                    &plain,
                    TableDefinitionKind::User,
                    None,
                    &mut offset,
                    &mut counter,
                )?;
                crate::definition::column_writer::write_column_record_with_context(
                    &mut BinaryWriter::new(&mut record, budget)?,
                    ordinal,
                    &plain,
                    resolved,
                    TableDefinitionKind::User,
                    order
                        .encoding_context()
                        .ok_or(UpdateError::Unsupported("column encoding context"))?,
                )?;
            }
            reserve(&mut edited.columns, 1, budget)?;
            edited.columns.push(crate::schema::definition::NamedRecord {
                record,
                name: column.name(),
            });
            edited.header[STORAGE_COLUMN_COUNT..STORAGE_COLUMN_COUNT + 2]
                .copy_from_slice(&(ordinal + 1).to_le_bytes());
            edited.header[COLUMN_COUNT..COLUMN_COUNT + 2]
                .copy_from_slice(&live_count.to_le_bytes());
            edited.header[STORAGE_VARIABLE_COUNT..STORAGE_VARIABLE_COUNT + 2]
                .copy_from_slice(&variables.to_le_bytes());
            let mut edits = PageEdits::new(database.geometry().page_count());
            if column.column_type().is_long_value() {
                let owned = crate::schema::map::create(database, &mut edits, &[], budget)?;
                let available = crate::schema::map::create(database, &mut edits, &[], budget)?;
                reserve(&mut edited.suffix, crate::LONG_VALUE_MAP_GROUP_LEN, budget)?;
                edited.suffix.extend_from_slice(&ordinal.to_le_bytes());
                for locator in [owned, available] {
                    edited.suffix.push(locator.row());
                    edited
                        .suffix
                        .extend_from_slice(&(locator.page().get() as u32).to_le_bytes()[..3]);
                }
            }
            edited.stage(database, &definition, &mut edits, budget)?;
            Ok((
                edits,
                (
                    catalog.root(),
                    row,
                    properties,
                    definition.root(),
                    live_count - 1,
                    auto_record,
                ),
            ))
        })?;
    crate::schema::edit::apply(file, journal, budget, |database, budget| {
        let definition = database.table_definition(root, budget)?;
        let mut layout = Vec::new();
        reserve(&mut layout, definition.columns().len(), budget)?;
        layout.extend(
            definition
                .columns()
                .iter()
                .map(crate::RowColumnLayout::from),
        );
        let nulls = [RowValue::Null; 255];
        let mut row = [0; crate::PAGE_BYTES];
        crate::encode_row(&layout, &nulls[..layout.len()], &mut row, budget)?;
        Ok((PageEdits::new(database.geometry().page_count()), ()))
    })?;
    if !properties.is_empty() {
        crate::schema::properties::store(file, journal, catalog_root, row, &properties, budget)?;
    }
    if let Some(record) = auto_record {
        backfill_auto(file, journal, root, ordinal, record, budget)?;
    }
    Ok(())
}

fn backfill_auto(
    file: &mut File,
    journal: &mut PageEdits,
    root: crate::PageNumber,
    ordinal: u16,
    record: [u8; 18],
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    let locators = crate::schema::edit::apply(file, journal, budget, |database, budget| {
        let table = database.table_definition(root, budget)?;
        let mut locators = Vec::new();
        let mut rows = database.rows(&table, budget)?;
        while let Some(mut row) = rows.next_row()? {
            reserve(&mut locators, 1, row.budget_mut())?;
            locators.push(row.locator());
        }
        drop(rows);
        // EXP-0299: backfilled rows cannot be checked against a stored rule.
        if !locators.is_empty() {
            let options = crate::properties::value_policy::options(database, &table, budget)?;
            crate::properties::value_policy::refuse_rules(&options, &table)?;
        }
        Ok((PageEdits::new(database.geometry().page_count()), locators))
    })?;
    let mut state = crate::write::auto_number::AutoNumberState::default();
    for row in locators {
        let (next, value) = state.allocate(None);
        state = next;
        crate::schema::edit::apply(file, journal, budget, |database, budget| {
            let table = database.table_definition(root, budget)?;
            let graph =
                crate::row::mutation_graph::RowGraph::load(database, &table, Some(row), budget)?;
            let edits = crate::write::field_update::plan_fields(
                database,
                &table,
                graph,
                row,
                &[(crate::ColumnOrdinal::new(ordinal), RowValue::Long(value))],
                budget,
            )?;
            Ok((edits, ()))
        })?;
    }
    crate::schema::edit::apply(file, journal, budget, |database, budget| {
        let table = database.table_definition(root, budget)?;
        let mut edited = crate::schema::definition::DefinitionEdit::new(&table, budget)?;
        edited
            .columns
            .get_mut(usize::from(ordinal))
            .ok_or(UpdateError::NotFound("AutoIncrement column"))?
            .record = record;
        let mut page = [0; crate::PAGE_BYTES];
        database.read_raw_page(root, &mut page, budget)?;
        state.write_bytes(&mut page, budget)?;
        edited
            .header
            .copy_from_slice(&page[..DEFINITION_HEADER_LEN]);
        let mut edits = PageEdits::new(database.geometry().page_count());
        edited.stage(database, &table, &mut edits, budget)?;
        Ok((edits, ()))
    })
}

pub(crate) fn spec(column: &ColumnDefinition) -> Result<ColumnSpec<'_>, UpdateError> {
    let width = || {
        u8::try_from(column.size())
            .ok()
            .and_then(std::num::NonZeroU8::new)
            .ok_or(UpdateError::Mismatch("column width"))
    };
    let kind = match column.physical_type() {
        ColumnPhysicalType::Boolean => ColumnType::Boolean,
        ColumnPhysicalType::Byte => ColumnType::Byte,
        ColumnPhysicalType::Integer => ColumnType::Integer,
        ColumnPhysicalType::Long if column.auto_increment() => ColumnType::AutoIncrement,
        ColumnPhysicalType::Long => ColumnType::Long,
        ColumnPhysicalType::Currency => ColumnType::Currency,
        ColumnPhysicalType::Single => ColumnType::Single,
        ColumnPhysicalType::Double => ColumnType::Double,
        ColumnPhysicalType::DateTime => ColumnType::DateTime,
        ColumnPhysicalType::Guid => ColumnType::Guid,
        ColumnPhysicalType::Text => {
            if matches!(column.storage(), ColumnStorageClass::Fixed { .. }) {
                ColumnType::FixedText { len: width()? }
            } else {
                ColumnType::Text { max_len: width()? }
            }
        }
        ColumnPhysicalType::Binary => ColumnType::Binary { max_len: width()? },
        ColumnPhysicalType::Memo => ColumnType::Memo,
        ColumnPhysicalType::LongBinary => ColumnType::LongBinary,
    };
    Ok(ColumnSpec::new(column.name().raw_bytes(), kind))
}

fn fixed_offset(
    table: &crate::TableDefinition,
    column: ColumnSpec<'_>,
    budget: &mut ResourceBudget,
) -> Result<u16, UpdateError> {
    let size = match column.column_type() {
        ColumnType::Boolean
        | ColumnType::Text { .. }
        | ColumnType::Binary { .. }
        | ColumnType::Memo
        | ColumnType::LongBinary => 0,
        ColumnType::FixedText { len } => u16::from(len.get()),
        ColumnType::Byte => 1,
        ColumnType::Integer => 2,
        ColumnType::Long | ColumnType::AutoIncrement | ColumnType::Single => 4,
        ColumnType::Currency | ColumnType::Double | ColumnType::DateTime => 8,
        ColumnType::Guid => 16,
    };
    let mut offset = 0_u16;
    if size == 0 {
        return Ok(offset);
    }
    loop {
        budget.charge_items(table.columns().len() as u64)?;
        let end = offset
            .checked_add(size)
            .ok_or(UpdateError::Unsupported("fixed column storage capacity"))?;
        let overlap = table
            .columns()
            .iter()
            .filter_map(|column| match column.storage() {
                ColumnStorageClass::Fixed { offset: start }
                    if column.physical_type() != ColumnPhysicalType::Boolean
                        && start < end
                        && offset < start + column.size() =>
                {
                    Some(start + column.size())
                }
                _ => None,
            })
            .max();
        match overlap {
            Some(next) => offset = next,
            None => return Ok(offset),
        }
    }
}
