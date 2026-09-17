//! Lossless LvProp field-name edits using EXP-0208/0266/0283/0297 framing.
use crate::page_edits::reserve;
use crate::{
    DatabaseReader, FileSource, InlineLongValue, LongValue, LongValueChunkValue, ResourceBudget,
    RowLocator, TableDefinition, TextCodePage, UpdateError, ValueKind,
};

pub(crate) fn load(
    database: &mut DatabaseReader<FileSource>,
    table: &TableDefinition,
    budget: &mut ResourceBudget,
) -> Result<(TableDefinition, RowLocator, Vec<u8>), UpdateError> {
    let catalog = crate::schema_catalog::table(database, b"MSysObjects", budget)?;
    let locator = crate::schema_catalog::object(database, &catalog, table.root(), budget)?;
    let property = crate::schema_catalog::column(&catalog, b"LvProp")?;
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
        crate::column_property_reader::decode(&bytes, table.columns(), budget)?;
    }
    Ok((catalog, locator, bytes))
}

pub(crate) fn rename(
    bytes: &[u8],
    old: &[u8],
    new: &[u8],
    budget: &mut ResourceBudget,
) -> Result<Vec<u8>, UpdateError> {
    let mut output = Vec::new();
    if bytes.is_empty() {
        return Ok(output);
    }
    let dictionary_end = 4 + u32_at(bytes, 4)? as usize;
    append(
        &mut output,
        bytes
            .get(..dictionary_end)
            .ok_or(UpdateError::Mismatch("property dictionary length"))?,
        budget,
    )?;
    let mut position = dictionary_end;
    while position < bytes.len() {
        let end = position
            .checked_add(u32_at(bytes, position)? as usize)
            .filter(|end| *end <= bytes.len())
            .ok_or(UpdateError::Mismatch("property block length"))?;
        let block = &bytes[position..end];
        let length = u16::from_le_bytes([
            *block
                .get(10)
                .ok_or(UpdateError::Mismatch("property field length"))?,
            *block
                .get(11)
                .ok_or(UpdateError::Mismatch("property field length"))?,
        ]) as usize;
        let field = block
            .get(12..12 + length)
            .ok_or(UpdateError::Mismatch("property field name"))?;
        if field == old {
            let new_length = block.len() - length + new.len();
            append(&mut output, &(new_length as u32).to_le_bytes(), budget)?;
            append(&mut output, &block[4..6], budget)?;
            append(&mut output, &(6 + new.len() as u32).to_le_bytes(), budget)?;
            append(&mut output, &(new.len() as u16).to_le_bytes(), budget)?;
            append(&mut output, new, budget)?;
            append(&mut output, &block[12 + length..], budget)?;
        } else {
            append(&mut output, block, budget)?;
        }
        position = end;
    }
    Ok(output)
}

fn u32_at(bytes: &[u8], offset: usize) -> Result<u32, UpdateError> {
    Ok(u32::from_le_bytes(
        bytes
            .get(offset..offset + 4)
            .and_then(|bytes| bytes.try_into().ok())
            .ok_or(UpdateError::Mismatch("property length word"))?,
    ))
}

fn append(
    output: &mut Vec<u8>,
    bytes: &[u8],
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    reserve(output, bytes.len(), budget)?;
    budget.charge_work_units(bytes.len() as u64)?;
    output.extend_from_slice(bytes);
    Ok(())
}

pub(crate) fn add(
    bytes: &[u8],
    column: crate::ColumnSpec<'_>,
    budget: &mut ResourceBudget,
) -> Result<Vec<u8>, UpdateError> {
    let mut dictionary = Vec::new();
    let mut names = Vec::new();
    let end = if bytes.is_empty() {
        0
    } else {
        4 + u32_at(bytes, 4)? as usize
    };
    if end > 0 {
        append(
            &mut dictionary,
            bytes
                .get(10..end)
                .ok_or(UpdateError::Mismatch("property dictionary"))?,
            budget,
        )?;
        let mut position = 10;
        while position < end {
            let length = bytes
                .get(position..position + 2)
                .ok_or(UpdateError::Mismatch("property dictionary name"))?;
            let length = u16::from_le_bytes([length[0], length[1]]) as usize;
            let name = bytes
                .get(position + 2..position + 2 + length)
                .filter(|_| position + 2 + length <= end)
                .ok_or(UpdateError::Mismatch("property dictionary name"))?;
            reserve(&mut names, 1, budget)?;
            names.push(name);
            position += 2 + length;
        }
    }
    let mut ordinals = [0_u16; 2];
    let requested = [b"Required".as_slice(), b"AllowZeroLength"];
    let property_count = 1 + usize::from(crate::column_properties::has_zero_length_property(
        column.physical_type(),
    ));
    for (property, &name) in requested[..property_count].iter().enumerate() {
        let ordinal = if let Some(ordinal) = names.iter().position(|&existing| existing == name) {
            ordinal
        } else {
            append(&mut dictionary, &(name.len() as u16).to_le_bytes(), budget)?;
            append(&mut dictionary, name, budget)?;
            reserve(&mut names, 1, budget)?;
            names.push(name);
            names.len() - 1
        };
        ordinals[property] = u16::try_from(ordinal)
            .map_err(|_| UpdateError::Unsupported("property dictionary capacity"))?;
    }
    let mut output = Vec::new();
    append(&mut output, b"KKD\0", budget)?;
    let length = u32::try_from(dictionary.len() + 6)
        .map_err(|_| UpdateError::Unsupported("property dictionary length"))?;
    append(&mut output, &length.to_le_bytes(), budget)?;
    append(&mut output, &[0x80, 0], budget)?;
    append(&mut output, &dictionary, budget)?;
    append(&mut output, &bytes[end..], budget)?;
    let length = 12 + column.name().len() + 9 * property_count;
    append(&mut output, &(length as u32).to_le_bytes(), budget)?;
    append(&mut output, &[1, 0], budget)?;
    append(
        &mut output,
        &(6 + column.name().len() as u32).to_le_bytes(),
        budget,
    )?;
    append(
        &mut output,
        &(column.name().len() as u16).to_le_bytes(),
        budget,
    )?;
    append(&mut output, column.name(), budget)?;
    let values = [column.required(), column.allow_zero_length()];
    for at in (0..property_count).rev() {
        append(&mut output, &[9, 0, 1, 1], budget)?;
        append(&mut output, &ordinals[at].to_le_bytes(), budget)?;
        append(
            &mut output,
            &[1, 0, if values[at] { 0xff } else { 0 }],
            budget,
        )?;
    }
    Ok(output)
}

pub(crate) fn remove(
    bytes: &[u8],
    selected: &[u8],
    budget: &mut ResourceBudget,
) -> Result<Vec<u8>, UpdateError> {
    let mut output = Vec::new();
    if bytes.is_empty() {
        return Ok(output);
    }
    let end = 4 + u32_at(bytes, 4)? as usize;
    append(
        &mut output,
        bytes
            .get(..end)
            .ok_or(UpdateError::Mismatch("property dictionary length"))?,
        budget,
    )?;
    let mut position = end;
    while position < bytes.len() {
        let end = position
            .checked_add(u32_at(bytes, position)? as usize)
            .filter(|end| *end <= bytes.len())
            .ok_or(UpdateError::Mismatch("property block length"))?;
        let block = &bytes[position..end];
        let length = block
            .get(10..12)
            .ok_or(UpdateError::Mismatch("property name length"))?;
        let length = u16::from_le_bytes([length[0], length[1]]) as usize;
        if block
            .get(12..12 + length)
            .ok_or(UpdateError::Mismatch("property name"))?
            != selected
        {
            append(&mut output, block, budget)?;
        }
        position = end;
    }
    Ok(output)
}

pub(crate) fn store(
    file: &mut std::fs::File,
    journal: &mut crate::page_edits::PageEdits,
    catalog: crate::PageNumber,
    row: RowLocator,
    bytes: &[u8],
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    crate::schema_publish::apply(file, journal, budget, |database, budget| {
        let catalog = database.table_definition(catalog, budget)?;
        let column = crate::schema_catalog::column(&catalog, b"LvProp")?;
        let graph =
            crate::row_mutation_graph::RowGraph::load(database, &catalog, Some(row), budget)?;
        let value = if bytes.is_empty() {
            crate::RowValue::Null
        } else {
            crate::RowValue::LongBinary(bytes)
        };
        let edits = crate::field_update::plan_fields(
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
