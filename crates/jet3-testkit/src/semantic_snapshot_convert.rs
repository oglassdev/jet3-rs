//! Conversions from `jet3` typed reader output to canonical snapshot objects.

use jet3::{
    ColumnDefinition, ColumnPhysicalType, DecodedValue, IndexDefinition, IndexDirection,
    InlineLongValue, LongValue, PageNumber, TableDefinition, ValueKind,
};

use super::retained::RetainedLedger;
use super::{SemanticSnapshotError, UnsupportedValueForm};
use crate::{
    Column, FiniteF32, FiniteF64, Guid, Index, IndexField, InvariantDecimal, PropertyMap,
    TypedValue,
};

/// DAO `DataTypeEnum` constant name for one Jet 3 physical column type.
const fn dao_type_name(physical_type: ColumnPhysicalType) -> &'static str {
    match physical_type {
        ColumnPhysicalType::Boolean => "dbBoolean",
        ColumnPhysicalType::Byte => "dbByte",
        ColumnPhysicalType::Integer => "dbInteger",
        ColumnPhysicalType::Long => "dbLong",
        ColumnPhysicalType::Currency => "dbCurrency",
        ColumnPhysicalType::Single => "dbSingle",
        ColumnPhysicalType::Double => "dbDouble",
        ColumnPhysicalType::DateTime => "dbDate",
        ColumnPhysicalType::Binary => "dbBinary",
        ColumnPhysicalType::Text => "dbText",
        ColumnPhysicalType::LongBinary => "dbLongBinary",
        ColumnPhysicalType::Memo => "dbMemo",
        ColumnPhysicalType::Guid => "dbGUID",
    }
}

/// Converts one column definition, retaining its raw record losslessly.
///
/// `nullable` and `required` are reported as unavailable: the recorded
/// provenance shows nullable and required columns with identical physical
/// records, so neither can be established from the definition alone.
pub(super) fn convert_column(
    column: &ColumnDefinition,
    name: String,
    ledger: &mut RetainedLedger,
) -> Result<Column, SemanticSnapshotError> {
    let mut properties = PropertyMap::new();
    let key = ledger.text("physical_type")?;
    ledger.insert(
        &mut properties,
        key,
        TypedValue::Byte {
            value: column.physical_type().raw(),
            raw_hex: None,
        },
    )?;
    let raw_record = ledger.hex(column.raw_record())?;
    let key = ledger.text("raw_record")?;
    ledger.insert(
        &mut properties,
        key,
        TypedValue::Binary {
            value: raw_record,
            raw_hex: None,
        },
    )?;
    Ok(Column {
        name,
        ordinal: u64::from(column.ordinal().get()),
        dao_type: ledger.text(dao_type_name(column.physical_type()))?,
        nullable: None,
        required: None,
        auto_increment: column.auto_increment(),
        size: Some(u64::from(column.size())),
        attributes: i64::from(column.raw_class_flags()),
        properties,
    })
}

/// Converts one ordinary or primary logical index through its physical record.
///
/// `ignore_nulls` is reported as unavailable because no recorded fact maps a
/// physical flag to it.
pub(super) fn convert_index(
    definition: &TableDefinition,
    logical: &IndexDefinition,
    primary: bool,
    column_names: &[String],
    table: PageNumber,
    ledger: &mut RetainedLedger,
) -> Result<Index, SemanticSnapshotError> {
    let missing = SemanticSnapshotError::InvalidIndexReference {
        table,
        physical_index: logical.physical_index(),
    };
    let physical = definition
        .physical_indexes()
        .get(usize::from(logical.physical_index()))
        .ok_or_else(|| missing.clone())?;
    let mut fields = Vec::new();
    for field in physical.fields() {
        let name = column_names
            .get(usize::from(field.column().get()))
            .ok_or_else(|| missing.clone())?;
        let name = ledger.text(name)?;
        ledger.push(
            &mut fields,
            IndexField {
                name,
                descending: field.direction() == IndexDirection::Descending,
            },
        )?;
    }
    let mut properties = PropertyMap::new();
    let key = ledger.text("raw_flags")?;
    ledger.insert(
        &mut properties,
        key,
        TypedValue::Byte {
            value: physical.raw_flags(),
            raw_hex: None,
        },
    )?;
    Ok(Index {
        name: ledger.ascii_name(logical.name().raw_bytes(), Some(table))?,
        primary,
        unique: physical.unique(),
        required: physical.required(),
        ignore_nulls: None,
        fields,
        properties,
    })
}

/// Converts one decoded field into its typed canonical value.
///
/// Every present value retains its exact physical bytes as `raw_hex`.
/// External long values, dates, and non-finite numbers fail closed.
pub(super) fn convert_value(
    decoded: &DecodedValue<'_>,
    table: PageNumber,
    column: u16,
    ledger: &mut RetainedLedger,
) -> Result<TypedValue, SemanticSnapshotError> {
    let unsupported = |form| SemanticSnapshotError::UnsupportedValue {
        table,
        column,
        form,
    };
    let raw_hex = match decoded.raw_bytes() {
        Some(bytes) => Some(ledger.hex(bytes)?),
        None => None,
    };
    Ok(match decoded.kind() {
        ValueKind::Null => TypedValue::Null { raw_hex: None },
        ValueKind::Boolean(value) => TypedValue::Boolean {
            value: *value,
            raw_hex: None,
        },
        ValueKind::Byte(value) => TypedValue::Byte {
            value: *value,
            raw_hex,
        },
        ValueKind::Integer(value) => TypedValue::Integer {
            value: *value,
            raw_hex,
        },
        ValueKind::Long(value) => TypedValue::Long {
            value: *value,
            raw_hex,
        },
        ValueKind::Currency(value) => TypedValue::Currency {
            value: currency_text(value.scaled(), value.scale(), ledger)?,
            raw_hex,
        },
        ValueKind::Single(value) => TypedValue::Single {
            value: FiniteF32::new(*value)
                .map_err(|_| unsupported(UnsupportedValueForm::NonFiniteNumber))?,
            raw_hex,
        },
        ValueKind::Double(value) => TypedValue::Double {
            value: FiniteF64::new(*value)
                .map_err(|_| unsupported(UnsupportedValueForm::NonFiniteNumber))?,
            raw_hex,
        },
        ValueKind::DateTime(_) => return Err(unsupported(UnsupportedValueForm::DateTime)),
        ValueKind::Binary(bytes) => TypedValue::Binary {
            value: ledger.hex(bytes)?,
            raw_hex,
        },
        ValueKind::Text(text) => TypedValue::Text {
            value: ledger.text(text.as_str())?,
            raw_hex,
            code_page: Some(u32::from(text.code_page().number())),
        },
        ValueKind::Guid(guid) => TypedValue::Guid {
            value: guid_text(guid.display_bytes(), ledger)?,
            raw_hex,
        },
        ValueKind::LongValue(LongValue::Inline { value, .. }) => match value {
            InlineLongValue::Text(text) => TypedValue::Memo {
                value: ledger.text(text.as_str())?,
                raw_hex,
                code_page: Some(u32::from(text.code_page().number())),
            },
            InlineLongValue::Binary(bytes) => TypedValue::Ole {
                value: ledger.hex(bytes)?,
                raw_hex,
            },
            _ => return Err(unsupported(UnsupportedValueForm::ExternalLongValue)),
        },
        ValueKind::LongValue(LongValue::External(_)) => {
            return Err(unsupported(UnsupportedValueForm::ExternalLongValue));
        }
        _ => return Err(unsupported(UnsupportedValueForm::ExternalLongValue)),
    })
}

/// Renders an exact scaled integer as an invariant decimal with a fixed scale.
fn currency_text(
    scaled: i64,
    scale: u32,
    ledger: &mut RetainedLedger,
) -> Result<InvariantDecimal, SemanticSnapshotError> {
    let divisor = 10_i128.pow(scale);
    let magnitude = i128::from(scaled).abs();
    let integer = magnitude / divisor;
    let fraction = magnitude % divisor;
    let sign = if scaled < 0 { "-" } else { "" };
    let width = scale as usize;
    ledger.charge(width + 22)?;
    Ok(InvariantDecimal::new(format!(
        "{sign}{integer}.{fraction:0width$}"
    ))?)
}

/// Renders display-ordered GUID bytes in the protocol's hyphenated form.
fn guid_text(bytes: [u8; 16], ledger: &mut RetainedLedger) -> Result<Guid, SemanticSnapshotError> {
    let hex = ledger.hex(&bytes)?;
    let hex = hex.as_str();
    ledger.charge(36)?;
    Ok(Guid::new(format!(
        "{}-{}-{}-{}-{}",
        &hex[0..8],
        &hex[8..12],
        &hex[12..16],
        &hex[16..20],
        &hex[20..32]
    ))?)
}
