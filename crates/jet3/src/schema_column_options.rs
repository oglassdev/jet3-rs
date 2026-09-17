//! EXP-0297 field Boolean edits affect future writes, preserving existing rows and other properties.
use crate::page_edits::{PageEdits, reserve};
use crate::{ResourceBudget, UpdateError};
use std::fs::File;

pub(crate) fn set(
    file: &mut File,
    journal: &mut PageEdits,
    table: &[u8],
    column: &[u8],
    required: Option<bool>,
    allow_zero_length: Option<bool>,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    let (catalog, row, bytes) =
        crate::schema_publish::apply(file, journal, budget, |database, budget| {
            let table = crate::update::indexed_writable_table(database, table, budget)?;
            let selected = table
                .columns()
                .iter()
                .find(|field| field.name().raw_bytes() == column)
                .ok_or(UpdateError::NotFound("column"))?;
            if allow_zero_length.is_some()
                && !crate::column_properties::has_zero_length_property(selected.physical_type())
            {
                return Err(UpdateError::Unsupported("AllowZeroLength column type"));
            }
            if required.is_none() && allow_zero_length.is_none() {
                return Err(UpdateError::Unsupported("no column options requested"));
            }
            let (catalog, row, bytes) = crate::schema_properties::load(database, &table, budget)?;
            let bytes = encode(&bytes, column, [required, allow_zero_length], budget)?;
            crate::column_property_reader::decode(&bytes, table.columns(), budget)?;
            Ok((
                PageEdits::new(database.geometry().page_count()),
                (catalog.root(), row, bytes),
            ))
        })?;
    crate::schema_properties::store(file, journal, catalog, row, &bytes, budget)
}

fn word(bytes: &[u8], offset: usize) -> Result<usize, UpdateError> {
    let value = bytes
        .get(offset..offset + 2)
        .ok_or(UpdateError::Mismatch("property word"))?;
    Ok(u16::from_le_bytes([value[0], value[1]]) as usize)
}
fn length(bytes: &[u8], offset: usize) -> Result<usize, UpdateError> {
    let value = bytes
        .get(offset..offset + 4)
        .and_then(|v| v.try_into().ok())
        .ok_or(UpdateError::Mismatch("property length"))?;
    Ok(u32::from_le_bytes(value) as usize)
}
fn append(out: &mut Vec<u8>, bytes: &[u8], budget: &mut ResourceBudget) -> Result<(), UpdateError> {
    reserve(out, bytes.len(), budget)?;
    budget.charge_work_units(bytes.len() as u64)?;
    out.extend_from_slice(bytes);
    Ok(())
}

fn encode(
    bytes: &[u8],
    column: &[u8],
    values: [Option<bool>; 2],
    budget: &mut ResourceBudget,
) -> Result<Vec<u8>, UpdateError> {
    let end = if bytes.is_empty() {
        0
    } else {
        4 + length(bytes, 4)?
    };
    let mut dictionary = Vec::new();
    let mut count = 0_u16;
    let mut ordinals = [None; 2];
    let names = [b"Required".as_slice(), b"AllowZeroLength"];
    if end != 0 {
        append(
            &mut dictionary,
            bytes
                .get(10..end)
                .ok_or(UpdateError::Mismatch("property dictionary bounds"))?,
            budget,
        )?;
        let mut position = 10;
        while position < end {
            let len = word(bytes, position)?;
            let name = bytes
                .get(position + 2..position + 2 + len)
                .ok_or(UpdateError::Mismatch("property name"))?;
            if let Some(at) = names.iter().position(|&known| known == name) {
                ordinals[at] = Some(count);
            }
            count = count
                .checked_add(1)
                .ok_or(UpdateError::Unsupported("property dictionary capacity"))?;
            position += 2 + len;
        }
    }
    for (at, name) in names.iter().enumerate() {
        if values[at].is_some() && ordinals[at].is_none() {
            append(&mut dictionary, &(name.len() as u16).to_le_bytes(), budget)?;
            append(&mut dictionary, name, budget)?;
            ordinals[at] = Some(count);
            count = count
                .checked_add(1)
                .ok_or(UpdateError::Unsupported("property dictionary capacity"))?;
        }
    }
    let mut output = Vec::new();
    append(&mut output, b"KKD\0", budget)?;
    append(
        &mut output,
        &(dictionary.len() as u32 + 6).to_le_bytes(),
        budget,
    )?;
    append(&mut output, &[0x80, 0], budget)?;
    append(&mut output, &dictionary, budget)?;
    let mut position = end;
    let mut found = false;
    while position < bytes.len() {
        let block_end = position
            .checked_add(length(bytes, position)?)
            .filter(|end| *end <= bytes.len())
            .ok_or(UpdateError::Mismatch("property block bounds"))?;
        let block = &bytes[position..block_end];
        let len = word(block, 10)?;
        let name = block
            .get(12..12 + len)
            .ok_or(UpdateError::Mismatch("property field name"))?;
        if name == column {
            write_block(
                &mut output,
                column,
                &block[12 + len..],
                values,
                ordinals,
                budget,
            )?;
            found = true;
        } else {
            append(&mut output, block, budget)?;
        }
        position = block_end;
    }
    if !found {
        write_block(&mut output, column, &[], values, ordinals, budget)?;
    }
    Ok(output)
}

fn write_block(
    output: &mut Vec<u8>,
    column: &[u8],
    existing: &[u8],
    values: [Option<bool>; 2],
    ordinals: [Option<u16>; 2],
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    let start = output.len();
    append(output, &[0; 4], budget)?;
    append(output, &[1, 0], budget)?;
    append(output, &(column.len() as u32 + 6).to_le_bytes(), budget)?;
    append(output, &(column.len() as u16).to_le_bytes(), budget)?;
    append(output, column, budget)?;
    let mut position = 0;
    let mut written = [false; 2];
    while position < existing.len() {
        let len = word(existing, position)?;
        let record = existing
            .get(position..position + len)
            .filter(|record| record.len() >= 8)
            .ok_or(UpdateError::Mismatch("property record bounds"))?;
        let ordinal = word(record, 4)? as u16;
        if let Some(at) = ordinals
            .iter()
            .position(|&selected| selected == Some(ordinal))
            && let Some(value) = values[at]
        {
            boolean(output, ordinal, value, budget)?;
            written[at] = true;
        } else {
            append(output, record, budget)?;
        }
        position += len;
    }
    for at in [1, 0] {
        if !written[at]
            && let (Some(ordinal), Some(value)) = (ordinals[at], values[at])
        {
            boolean(output, ordinal, value, budget)?;
        }
    }
    let len = u32::try_from(output.len() - start)
        .map_err(|_| UpdateError::Unsupported("property block size"))?;
    output[start..start + 4].copy_from_slice(&len.to_le_bytes());
    Ok(())
}
fn boolean(
    output: &mut Vec<u8>,
    ordinal: u16,
    value: bool,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    append(output, &[9, 0, 1, 1], budget)?;
    append(output, &ordinal.to_le_bytes(), budget)?;
    append(output, &[1, 0, if value { 0xff } else { 0 }], budget)
}
