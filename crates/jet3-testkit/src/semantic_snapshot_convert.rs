//! Conversions from `jet3` typed reader output to canonical snapshot objects.

use jet3::{
    ColumnDefinition, ColumnPhysicalType, DateTimeValue, DecodedValue, IndexDefinition,
    IndexDirection, InlineLongValue, LongValue, PageNumber, TableDefinition, ValueKind,
};

use super::retained::RetainedLedger;
use super::{SemanticSnapshotError, UnsupportedValueForm};
use crate::{
    FiniteF32, FiniteF64, Guid, IndexField, InvariantDateTime, InvariantDecimal, PropertyMap,
    SemanticColumn, SemanticIndex, TypedValue,
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
) -> Result<SemanticColumn, SemanticSnapshotError> {
    Ok(SemanticColumn {
        name,
        ordinal: u64::from(column.ordinal().get()),
        dao_type: ledger.text(dao_type_name(column.physical_type()))?,
        auto_increment: column.auto_increment(),
        size: Some(u64::from(column.size())),
        attributes: i64::from(column.raw_class_flags()),
        properties: PropertyMap::new(),
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
) -> Result<SemanticIndex, SemanticSnapshotError> {
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
    Ok(SemanticIndex {
        name: ledger.ascii_name(logical.name().raw_bytes(), Some(table))?,
        primary,
        unique: physical.unique(),
        required: physical.required(),
        fields,
        properties: PropertyMap::new(),
    })
}

/// Converts one decoded field into its typed canonical value.
///
/// Every present value retains its exact physical bytes as `raw_hex`.
/// External long values are streamed by the caller; non-finite numbers fail closed.
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
        ValueKind::DateTime(value) => TypedValue::DateTime {
            value: datetime_text(*value, table, column, ledger)?,
            raw_hex,
        },
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

/// Converts OLE Automation day semantics from `SRC-0026` into invariant text.
fn datetime_text(
    value: DateTimeValue,
    table: PageNumber,
    column: u16,
    ledger: &mut RetainedLedger,
) -> Result<InvariantDateTime, SemanticSnapshotError> {
    let invalid = || SemanticSnapshotError::UnsupportedValue {
        table,
        column,
        form: UnsupportedValueForm::DateTime,
    };
    let days = value.days();
    if !days.is_finite() || !(-657_435.0..=2_958_465.999_999_99).contains(&days) {
        return Err(invalid());
    }
    let mut ordinal = days_before_year(1899) + days_before_month(1899, 12) + 29;
    ordinal += days.trunc() as i64;
    let mut nanoseconds = (days.fract().abs() * 86_400_000_000_000.0).round() as u64;
    if nanoseconds == 86_400_000_000_000 {
        ordinal += 1;
        nanoseconds = 0;
    }
    let (year, month, day) = date_from_ordinal(ordinal).ok_or_else(invalid)?;
    let hour = nanoseconds / 3_600_000_000_000;
    nanoseconds %= 3_600_000_000_000;
    let minute = nanoseconds / 60_000_000_000;
    nanoseconds %= 60_000_000_000;
    let second = nanoseconds / 1_000_000_000;
    let fraction = nanoseconds % 1_000_000_000;
    let retained_len = if fraction == 0 {
        19
    } else {
        let mut significant = fraction;
        let mut trailing_zeroes = 0_usize;
        while significant.is_multiple_of(10) {
            significant /= 10;
            trailing_zeroes += 1;
        }
        29 - trailing_zeroes
    };
    ledger.charge(retained_len)?;
    let mut text = format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}");
    if fraction != 0 {
        let digits = format!("{fraction:09}");
        text.push('.');
        text.push_str(digits.trim_end_matches('0'));
    }
    debug_assert_eq!(text.len(), retained_len);
    InvariantDateTime::new(text).map_err(|_| invalid())
}

const fn leap_year(year: i64) -> bool {
    year % 4 == 0 && (year % 100 != 0 || year % 400 == 0)
}

const fn days_before_year(year: i64) -> i64 {
    let prior = year - 1;
    prior * 365 + prior / 4 - prior / 100 + prior / 400
}

fn days_before_month(year: i64, month: u8) -> i64 {
    const STARTS: [i64; 12] = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];
    STARTS[usize::from(month - 1)] + i64::from(month > 2 && leap_year(year))
}

fn date_from_ordinal(ordinal: i64) -> Option<(i64, u8, u8)> {
    if !(0..days_before_year(10_000)).contains(&ordinal) {
        return None;
    }
    let mut low = 1_i64;
    let mut high = 10_000_i64;
    while low + 1 < high {
        let middle = (low + high) / 2;
        if days_before_year(middle) <= ordinal {
            low = middle;
        } else {
            high = middle;
        }
    }
    let day_of_year = ordinal - days_before_year(low);
    let mut month = 1_u8;
    while month < 12 && days_before_month(low, month + 1) <= day_of_year {
        month += 1;
    }
    Some((
        low,
        month,
        u8::try_from(day_of_year - days_before_month(low, month) + 1).ok()?,
    ))
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
