//! Conversions from `jet3` typed reader output to canonical snapshot objects.

use jet3::{
    ColumnDefinition, ColumnPhysicalType, DecodedValue, IndexDefinition, IndexDirection,
    InlineLongValue, LongValue, PageNumber, TableDefinition, ValueKind,
};

use super::{SemanticSnapshotError, UnsupportedValueForm};
use crate::{
    Column, FiniteF32, FiniteF64, Guid, HexString, Index, IndexField, InvariantDecimal,
    PropertyMap, TypedValue,
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

/// Accepts only ASCII definition names; other bytes need an established code
/// page before they can be represented as snapshot identifiers.
pub(super) fn decode_ascii_name(
    raw: &[u8],
    table: Option<PageNumber>,
) -> Result<String, SemanticSnapshotError> {
    if !raw.is_ascii() {
        return Err(SemanticSnapshotError::NonAsciiName { table });
    }
    String::from_utf8(raw.to_vec()).map_err(|_| SemanticSnapshotError::NonAsciiName { table })
}

/// Converts one column definition, retaining its raw record losslessly.
///
/// `required` is not decoded by the reader yet and is reported as `false`;
/// the lossless raw class flags are exposed as `attributes` so a differential
/// comparison surfaces any divergence instead of hiding it.
pub(super) fn convert_column(column: &ColumnDefinition, name: String) -> Column {
    let mut properties = PropertyMap::new();
    properties.insert(
        "physical_type".to_owned(),
        TypedValue::Byte {
            value: column.physical_type().raw(),
            raw_hex: None,
        },
    );
    properties.insert(
        "raw_record".to_owned(),
        TypedValue::Binary {
            value: HexString::from_bytes(column.raw_record()),
            raw_hex: None,
        },
    );
    Column {
        name,
        ordinal: u64::from(column.ordinal().get()),
        dao_type: dao_type_name(column.physical_type()).to_owned(),
        nullable: column.physical_type() != ColumnPhysicalType::Boolean,
        required: false,
        auto_increment: column.auto_increment(),
        size: Some(u64::from(column.size())),
        attributes: i64::from(column.raw_class_flags()),
        properties,
    }
}

/// Converts one ordinary or primary logical index through its physical record.
pub(super) fn convert_index(
    definition: &TableDefinition,
    logical: &IndexDefinition,
    primary: bool,
    column_names: &[String],
    table: PageNumber,
) -> Result<Index, SemanticSnapshotError> {
    let missing = SemanticSnapshotError::InvalidIndexReference {
        table,
        physical_index: logical.physical_index(),
    };
    let physical = definition
        .physical_indexes()
        .get(usize::from(logical.physical_index()))
        .ok_or_else(|| missing.clone())?;
    let fields = physical
        .fields()
        .iter()
        .map(|field| {
            column_names
                .get(usize::from(field.column().get()))
                .map(|name| IndexField {
                    name: name.clone(),
                    descending: field.direction() == IndexDirection::Descending,
                })
                .ok_or_else(|| missing.clone())
        })
        .collect::<Result<Vec<_>, _>>()?;
    let mut properties = PropertyMap::new();
    properties.insert(
        "raw_flags".to_owned(),
        TypedValue::Byte {
            value: physical.raw_flags(),
            raw_hex: None,
        },
    );
    Ok(Index {
        name: decode_ascii_name(logical.name().raw_bytes(), Some(table))?,
        primary,
        unique: physical.unique(),
        required: physical.required(),
        ignore_nulls: false,
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
) -> Result<TypedValue, SemanticSnapshotError> {
    let unsupported = |form| SemanticSnapshotError::UnsupportedValue {
        table,
        column,
        form,
    };
    let raw_hex = decoded.raw_bytes().map(HexString::from_bytes);
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
            value: currency_text(value.scaled(), value.scale())?,
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
            value: HexString::from_bytes(bytes),
            raw_hex,
        },
        ValueKind::Text(text) => TypedValue::Text {
            value: text.as_str().to_owned(),
            raw_hex,
            code_page: Some(u32::from(text.code_page().number())),
        },
        ValueKind::Guid(guid) => TypedValue::Guid {
            value: guid_text(guid.display_bytes())?,
            raw_hex,
        },
        ValueKind::LongValue(LongValue::Inline { value, .. }) => match value {
            InlineLongValue::Text(text) => TypedValue::Memo {
                value: text.as_str().to_owned(),
                raw_hex,
                code_page: Some(u32::from(text.code_page().number())),
            },
            InlineLongValue::Binary(bytes) => TypedValue::Ole {
                value: HexString::from_bytes(bytes),
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
fn currency_text(scaled: i64, scale: u32) -> Result<InvariantDecimal, SemanticSnapshotError> {
    let divisor = 10_i128.pow(scale);
    let magnitude = i128::from(scaled).abs();
    let integer = magnitude / divisor;
    let fraction = magnitude % divisor;
    let sign = if scaled < 0 { "-" } else { "" };
    let width = scale as usize;
    Ok(InvariantDecimal::new(format!(
        "{sign}{integer}.{fraction:0width$}"
    ))?)
}

/// Renders display-ordered GUID bytes in the protocol's hyphenated form.
fn guid_text(bytes: [u8; 16]) -> Result<Guid, SemanticSnapshotError> {
    let hex = HexString::from_bytes(&bytes);
    let hex = hex.as_str();
    Ok(Guid::new(format!(
        "{}-{}-{}-{}-{}",
        &hex[0..8],
        &hex[8..12],
        &hex[12..16],
        &hex[16..20],
        &hex[20..32]
    ))?)
}
