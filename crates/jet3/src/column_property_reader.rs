//! Checked property semantics over lossless LvProp blobs: EXP-0208/0266/0283/0285
//! Boolean records and EXP-0299 field and table validation rules.
use crate::property_blob::{BOOLEAN, FIELD_BLOCK, MEMO, PropertyBlob, Record, TABLE_BLOCK, TEXT};
use crate::{ColumnDefinition, ColumnPropertyError, ResourceBudget};

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub(crate) struct ColumnOptions {
    pub required: bool,
    pub allow_zero_length: Option<bool>,
    /// A nonempty ValidationRule is stored for this column.
    pub validation_rule: bool,
}

/// Checked options for every live column plus the table-level rule.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct PropertyOptions {
    pub columns: [ColumnOptions; 255],
    pub validation_rule: bool,
}

impl Default for PropertyOptions {
    fn default() -> Self {
        Self {
            columns: [ColumnOptions::default(); 255],
            validation_rule: false,
        }
    }
}

impl PropertyOptions {
    /// The first stored nonempty rule: `Some(None)` for the table, `Some(Some(ordinal))` for a column.
    pub(crate) fn validation_rule(&self, columns: &[ColumnDefinition]) -> Option<Option<u16>> {
        if self.validation_rule {
            return Some(None);
        }
        columns
            .iter()
            .zip(self.columns)
            .find(|(_, options)| options.validation_rule)
            .map(|(column, _)| Some(column.ordinal().get()))
    }
}

fn require(valid: bool, detail: &'static str) -> Result<(), ColumnPropertyError> {
    if valid {
        Ok(())
    } else {
        Err(ColumnPropertyError::Invalid(detail))
    }
}

fn unique(
    blob: &PropertyBlob,
    name: &[u8],
    detail: &'static str,
) -> Result<Option<u16>, ColumnPropertyError> {
    let mut found = None;
    for (ordinal, existing) in blob.names().iter().enumerate() {
        if existing == name {
            require(found.is_none(), detail)?;
            found = u16::try_from(ordinal).ok();
        }
    }
    Ok(found)
}

fn boolean(record: &Record) -> Result<bool, ColumnPropertyError> {
    let value = record
        .value()
        .filter(|_| record.flag() == 1 && record.kind() == BOOLEAN);
    match value {
        Some([0]) => Ok(false),
        Some([0xff]) => Ok(true),
        _ => Err(ColumnPropertyError::Invalid(
            "named Boolean property record",
        )),
    }
}

/// EXP-0299 stores rules as Text or Memo; DAO may append one NUL, and an empty value is no rule.
pub(crate) fn text(record: &Record) -> Result<&[u8], ColumnPropertyError> {
    record
        .value()
        .filter(|_| matches!(record.kind(), TEXT | MEMO))
        .ok_or(ColumnPropertyError::Invalid("text property record"))
}

pub(crate) fn decode(
    data: &[u8],
    columns: &[ColumnDefinition],
    budget: &mut ResourceBudget,
) -> Result<PropertyOptions, ColumnPropertyError> {
    let blob = PropertyBlob::parse(data, budget)?;
    options(&blob, columns, budget)
}

pub(crate) fn options(
    blob: &PropertyBlob,
    columns: &[ColumnDefinition],
    budget: &mut ResourceBudget,
) -> Result<PropertyOptions, ColumnPropertyError> {
    require(columns.len() <= 255, "property column count")?;
    budget.charge_work_units(
        (blob.len() as u64)
            .checked_mul(columns.len() as u64 + 1)
            .ok_or(ColumnPropertyError::Invalid("property work overflow"))?,
    )?;
    let known = [
        unique(blob, b"Required", "duplicate Boolean property name")?,
        unique(blob, b"AllowZeroLength", "duplicate Boolean property name")?,
        unique(
            blob,
            b"ValidationRule",
            "duplicate ValidationRule property name",
        )?,
    ];
    let mut result = PropertyOptions::default();
    let mut seen = [false; 255];
    let mut table_seen = false;
    for block in blob.blocks() {
        let column = if block.kind() == FIELD_BLOCK {
            columns
                .iter()
                .position(|column| column.name().raw_bytes() == block.name())
        } else {
            None
        };
        if let Some(column) = column {
            require(!seen[column], "duplicate field property block")?;
            seen[column] = true;
        }
        let table = block.kind() == TABLE_BLOCK && block.name().is_empty();
        if table {
            require(!table_seen, "duplicate table property block")?;
            table_seen = true;
        }
        let mut seen_properties = [false; 3];
        for record in block.records() {
            let Some(property) = known.iter().position(|known| *known == Some(record.name()))
            else {
                continue;
            };
            require(!seen_properties[property], "duplicate property record")?;
            seen_properties[property] = true;
            if property == 2 {
                let present = text(record)?.iter().any(|byte| *byte != 0);
                if let Some(column) = column {
                    result.columns[column].validation_rule = present;
                } else if table {
                    result.validation_rule = present;
                }
                continue;
            }
            let value = boolean(record)?;
            if let Some(column) = column {
                if property == 0 {
                    result.columns[column].required = value;
                } else {
                    require(
                        crate::column_properties::has_zero_length_property(
                            columns[column].physical_type(),
                        ),
                        "AllowZeroLength column type",
                    )?;
                    result.columns[column].allow_zero_length = Some(value);
                }
            }
        }
    }
    Ok(result)
}
