//! Lossless LvProp field-block edits using EXP-0208/0266/0283/0297 framing and EXP-0299 text records.
use crate::{
    DatabaseReader, FileSource, InlineLongValue, LongValue, LongValueChunkValue, ResourceBudget,
    RowLocator, TableDefinition, TextCodePage, UpdateError, ValueKind,
    properties::{
        blob::{BOOLEAN, Block, FIELD_BLOCK, PropertyBlob, Record},
        column::TextProperty,
    },
    write::page_edits::reserve,
};

pub(crate) fn load(
    database: &mut DatabaseReader<FileSource>,
    table: &TableDefinition,
    budget: &mut ResourceBudget,
) -> Result<(TableDefinition, RowLocator, Vec<u8>), UpdateError> {
    let catalog = crate::schema::catalog::table(database, b"MSysObjects", budget)?;
    let locator = crate::schema::catalog::object(database, &catalog, table.root(), budget)?;
    let property = crate::schema::catalog::column(&catalog, b"LvProp")?;
    let mut bytes = Vec::new();
    let mut reference = None;
    let mut rows = database.rows(&catalog, budget)?;
    while let Some(mut row) = rows.next_row()? {
        if row.locator() != locator {
            continue;
        }
        let mut saved = [0; crate::PAGE_BYTES];
        let length = match row
            .value(property, TextCodePage::Windows1252)?
            .ok_or(UpdateError::NotFound("catalog properties"))?
            .kind()
        {
            ValueKind::Null => 0,
            ValueKind::LongValue(LongValue::External(value)) => {
                reference = Some(*value);
                0
            }
            ValueKind::LongValue(LongValue::Inline { value, .. }) => {
                let source = match value {
                    InlineLongValue::Binary(bytes) => bytes,
                    InlineLongValue::Text(text) => text.raw_bytes(),
                };
                saved
                    .get_mut(..source.len())
                    .ok_or(UpdateError::Mismatch("inline properties length"))?
                    .copy_from_slice(source);
                source.len()
            }
            _ => return Err(UpdateError::Mismatch("catalog property type")),
        };
        reserve(&mut bytes, length, row.budget_mut())?;
        bytes.extend_from_slice(&saved[..length]);
        break;
    }
    if let Some(reference) = reference {
        let mut stream = rows.long_value(reference)?;
        reserve(&mut bytes, reference.length() as usize, stream.budget_mut())?;
        while let Some(chunk) = stream.next_chunk()? {
            let data = match chunk.value() {
                LongValueChunkValue::Binary(bytes) => bytes,
                LongValueChunkValue::Text(text) => text.raw_bytes(),
            };
            if bytes.len().saturating_add(data.len()) > reference.length() as usize {
                return Err(UpdateError::Mismatch("property length"));
            }
            bytes.extend_from_slice(data);
        }
    }
    drop(rows);
    if !bytes.is_empty() {
        crate::properties::reader::decode(&bytes, table.columns(), budget)?;
    }
    Ok((catalog, locator, bytes))
}

fn parse(bytes: &[u8], budget: &mut ResourceBudget) -> Result<PropertyBlob, UpdateError> {
    if bytes.is_empty() {
        Ok(PropertyBlob::empty())
    } else {
        Ok(PropertyBlob::parse(bytes, budget)?)
    }
}

pub(crate) fn encode(
    blob: &PropertyBlob,
    budget: &mut ResourceBudget,
) -> Result<Vec<u8>, UpdateError> {
    Ok(blob.encode(budget)?)
}

/// Renames the column's field block, retaining every record byte (EXP-0297).
pub(crate) fn rename(
    bytes: &[u8],
    old: &[u8],
    new: &[u8],
    budget: &mut ResourceBudget,
) -> Result<Vec<u8>, UpdateError> {
    if bytes.is_empty() {
        return Ok(Vec::new());
    }
    let mut blob = parse(bytes, budget)?;
    if let Some(block) = blob.block_mut(FIELD_BLOCK, old) {
        block.rename(new, budget)?;
    }
    encode(&blob, budget)
}

/// Appends the new column's block after existing blocks, as for EXP-0297 appends.
pub(crate) fn add(
    bytes: &[u8],
    column: crate::ColumnSpec<'_>,
    budget: &mut ResourceBudget,
) -> Result<Vec<u8>, UpdateError> {
    let auto = column.column_type() == crate::ColumnType::AutoIncrement;
    if auto
        && TextProperty::FIELD_ORDER
            .iter()
            .all(|property| property.of(&column).is_none())
    {
        // EXP-0283: AutoIncrement has no Boolean block.
        return Ok(crate::properties::blob::owned(bytes, budget)?);
    }
    let mut blob = parse(bytes, budget)?;
    let eligible = crate::properties::column::has_zero_length_property(column.physical_type());
    // EXP-0299: absent dictionary names are appended in record order.
    let allow = if eligible && !auto {
        Some(blob.intern(b"AllowZeroLength", budget)?)
    } else {
        None
    };
    let required = if auto {
        None
    } else {
        Some(blob.intern(b"Required", budget)?)
    };
    let mut block = Block::new(FIELD_BLOCK, column.name(), budget)?;
    if let Some(name) = allow {
        block.set(boolean(name, column.allow_zero_length(), budget)?, budget)?;
    }
    if let Some(name) = required {
        block.set(boolean(name, column.required(), budget)?, budget)?;
    }
    for property in TextProperty::FIELD_ORDER {
        if let Some(value) = property.of(&column) {
            let name = blob.intern(property.name(), budget)?;
            let record = Record::new(property.flag(), property.field_kind(), name, value, budget)?;
            block.set(record, budget)?;
        }
    }
    if !block.records().is_empty() {
        blob.push(block, budget)?;
    }
    encode(&blob, budget)
}

pub(crate) fn boolean(
    name: u16,
    value: bool,
    budget: &mut ResourceBudget,
) -> Result<Record, UpdateError> {
    Ok(Record::new(
        1,
        BOOLEAN,
        name,
        &[if value { 0xff } else { 0 }],
        budget,
    )?)
}

/// Removes the column's field block; dictionary names are retained.
pub(crate) fn remove(
    bytes: &[u8],
    selected: &[u8],
    budget: &mut ResourceBudget,
) -> Result<Vec<u8>, UpdateError> {
    if bytes.is_empty() {
        return Ok(Vec::new());
    }
    let mut blob = parse(bytes, budget)?;
    blob.retain_blocks(|block| block.kind() != FIELD_BLOCK || block.name() != selected);
    encode(&blob, budget)
}

pub(crate) fn store(
    file: &mut std::fs::File,
    journal: &mut crate::write::page_edits::PageEdits,
    catalog: crate::PageNumber,
    row: RowLocator,
    bytes: &[u8],
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    crate::schema::edit::apply(file, journal, budget, |database, budget| {
        let catalog = database.table_definition(catalog, budget)?;
        let column = crate::schema::catalog::column(&catalog, b"LvProp")?;
        let graph =
            crate::row::mutation_graph::RowGraph::load(database, &catalog, Some(row), budget)?;
        let value = if bytes.is_empty() {
            crate::RowValue::Null
        } else {
            crate::RowValue::LongBinary(bytes)
        };
        let edits = crate::write::field_update::plan_fields(
            database,
            &catalog,
            graph,
            row,
            &[(column, value)],
            budget,
        )?;
        Ok((edits, ()))
    })
}
