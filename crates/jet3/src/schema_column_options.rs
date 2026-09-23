//! EXP-0297 Boolean and EXP-0299 text property edits. They affect future writes
//! only, preserving existing rows and every unrelated LvProp byte.
use crate::column_properties::TextProperty;
use crate::page_edits::PageEdits;
use crate::property_blob::{Block, FIELD_BLOCK, MEMO, PropertyBlob, Record, TABLE_BLOCK, TEXT};
use crate::{PropertyChange, ResourceBudget, UpdateError};
use std::fs::File;

/// Requested changes to one field or, when `column` is `None`, the table.
#[derive(Debug, Clone, Copy)]
pub(crate) struct PropertyEdit<'a> {
    pub column: Option<&'a [u8]>,
    pub required: Option<bool>,
    pub allow_zero_length: Option<bool>,
    /// Changes in [`TextProperty::FIELD_ORDER`].
    pub text: [PropertyChange<'a>; 4],
}

pub(crate) fn set(
    file: &mut File,
    journal: &mut PageEdits,
    table: &[u8],
    edit: PropertyEdit<'_>,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    let (catalog, row, bytes) =
        crate::schema_publish::apply(file, journal, budget, |database, budget| {
            let table = crate::update::indexed_writable_table(database, table, budget)?;
            if edit.required.is_none()
                && edit.allow_zero_length.is_none()
                && edit
                    .text
                    .iter()
                    .all(|change| matches!(change, PropertyChange::Keep))
            {
                return Err(UpdateError::Unsupported("no properties requested"));
            }
            for (property, change) in TextProperty::FIELD_ORDER.into_iter().zip(edit.text) {
                if let PropertyChange::Set(value) = change {
                    crate::column_properties::check_value(value)
                        .map_err(UpdateError::Unsupported)?;
                    if edit.column.is_none()
                        && !matches!(
                            property,
                            TextProperty::ValidationRule | TextProperty::ValidationText
                        )
                    {
                        return Err(UpdateError::Unsupported("table property name"));
                    }
                }
            }
            if let Some(column) = edit.column {
                let selected = table
                    .columns()
                    .iter()
                    .find(|field| field.name().raw_bytes() == column)
                    .ok_or(UpdateError::NotFound("column"))?;
                let kind = selected.physical_type();
                if edit.allow_zero_length.is_some()
                    && !crate::column_properties::has_zero_length_property(kind)
                {
                    return Err(UpdateError::Unsupported("AllowZeroLength column type"));
                }
                if TextProperty::FIELD_ORDER
                    .into_iter()
                    .zip(edit.text)
                    .any(|(property, change)| {
                        matches!(change, PropertyChange::Set(_)) && !property.eligible(kind)
                    })
                {
                    return Err(UpdateError::Unsupported("validation property column type"));
                }
            }
            let (catalog, row, bytes) = crate::schema_properties::load(database, &table, budget)?;
            let mut blob = if bytes.is_empty() {
                PropertyBlob::empty()
            } else {
                PropertyBlob::parse(&bytes, budget)?
            };
            apply(&mut blob, edit, budget)?;
            let bytes = if bytes.is_empty() && blob.blocks().is_empty() {
                Vec::new()
            } else {
                crate::schema_properties::encode(&blob, budget)?
            };
            if !bytes.is_empty() {
                crate::column_property_reader::decode(&bytes, table.columns(), budget)?;
            }
            Ok((
                PageEdits::new(database.geometry().page_count()),
                (catalog.root(), row, bytes),
            ))
        })?;
    crate::schema_properties::store(file, journal, catalog, row, &bytes, budget)
}

fn apply(
    blob: &mut PropertyBlob,
    edit: PropertyEdit<'_>,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    let (kind, name) = match edit.column {
        Some(column) => (FIELD_BLOCK, column),
        None => (TABLE_BLOCK, b"".as_slice()),
    };
    // EXP-0297: new Boolean names append Required before AllowZeroLength.
    let required = match edit.required {
        Some(_) => Some(blob.intern(b"Required", budget)?),
        None => None,
    };
    let allow = match edit.allow_zero_length {
        Some(_) => Some(blob.intern(b"AllowZeroLength", budget)?),
        None => None,
    };
    let mut records = [None, None, None, None];
    for ((property, change), slot) in TextProperty::FIELD_ORDER
        .into_iter()
        .zip(edit.text)
        .zip(&mut records)
    {
        *slot = match change {
            PropertyChange::Keep => None,
            PropertyChange::Clear => blob.ordinal(property.name()).map(|name| (name, None)),
            PropertyChange::Set(value) => {
                let name = blob.intern(property.name(), budget)?;
                Some((
                    name,
                    Some(text_record(property, kind, name, value, budget)?),
                ))
            }
        };
    }
    let position = match blob
        .blocks()
        .iter()
        .position(|block| block.kind() == kind && block.name() == name)
    {
        Some(position) => position,
        None => {
            if records.iter().flatten().all(|(_, record)| record.is_none())
                && required.is_none()
                && allow.is_none()
            {
                return Ok(());
            }
            blob.push(Block::new(kind, name, budget)?, budget)?;
            blob.blocks().len() - 1
        }
    };
    let block = blob.block_at(position)?;
    // EXP-0297: an absent AllowZeroLength record is appended before Required.
    for (name, value) in [(allow, edit.allow_zero_length), (required, edit.required)] {
        if let (Some(name), Some(value)) = (name, value) {
            block.set(
                crate::schema_properties::boolean(name, value, budget)?,
                budget,
            )?;
        }
    }
    for (name, record) in records.into_iter().flatten() {
        match record {
            Some(record) => block.set(record, budget)?,
            // EXP-0299: assigning an empty value removes the record.
            None => block.remove(name),
        }
    }
    if block.records().is_empty() {
        // EXP-0299: clearing the last table property removes its block.
        blob.retain_blocks(|block| !(block.kind() == kind && block.name() == name));
    }
    Ok(())
}

/// EXP-0299 existing-object assignments: field rules gain one NUL and table rules are Memo.
fn text_record(
    property: TextProperty,
    block: u16,
    name: u16,
    value: &[u8],
    budget: &mut ResourceBudget,
) -> Result<Record, UpdateError> {
    let record = if block == TABLE_BLOCK {
        let kind = if property == TextProperty::ValidationRule {
            MEMO
        } else {
            TEXT
        };
        Record::new(property.flag(), kind, name, value, budget)?
    } else if property == TextProperty::ValidationRule {
        let mut terminated = crate::property_blob::owned(value, budget)?;
        crate::resource::reserve(&mut terminated, 1, budget)?;
        terminated.push(0);
        Record::new(
            property.flag(),
            property.field_kind(),
            name,
            &terminated,
            budget,
        )?
    } else {
        Record::new(property.flag(), property.field_kind(), name, value, budget)?
    };
    Ok(record)
}
