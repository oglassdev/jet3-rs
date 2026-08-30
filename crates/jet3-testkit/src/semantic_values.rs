//! Canonical spellings of `jet3` reader values and column facts.

use jet3::{
    ColumnDefinition, ColumnPhysicalType, ColumnStorageClass, DecodedText, DecodedValue,
    InlineLongValue, LongValue, LongValueReference, ValueKind,
};
use serde_json::value::RawValue;

use crate::{Column, PropertyMap, Scalar, SnapshotError, TypedValue, hex};

/// DAO `DataTypeEnum` constant name for one Jet 3 physical column type.
#[must_use]
pub const fn dao_type_name(physical_type: ColumnPhysicalType) -> &'static str {
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

/// DAO `Field.Size` for a column: the fixed width, the declared size for
/// Text/Binary, and zero for long values.
#[must_use]
pub const fn normalized_size(physical_type: ColumnPhysicalType, declared: u64) -> u64 {
    match physical_type {
        ColumnPhysicalType::Boolean | ColumnPhysicalType::Byte => 1,
        ColumnPhysicalType::Integer => 2,
        ColumnPhysicalType::Long | ColumnPhysicalType::Single => 4,
        ColumnPhysicalType::Currency
        | ColumnPhysicalType::Double
        | ColumnPhysicalType::DateTime => 8,
        ColumnPhysicalType::Guid => 16,
        ColumnPhysicalType::Binary | ColumnPhysicalType::Text => declared,
        ColumnPhysicalType::LongBinary | ColumnPhysicalType::Memo => 0,
    }
}

/// DAO `Field.Attributes`: `dbFixedField` (1) or `dbVariableField` (2), plus
/// `dbAutoIncrField` (16).
#[must_use]
pub const fn normalized_attributes(storage: ColumnStorageClass, auto_increment: bool) -> i64 {
    let storage = match storage {
        ColumnStorageClass::Fixed { .. } => 1,
        ColumnStorageClass::Variable { .. } => 2,
    };
    storage | if auto_increment { 16 } else { 0 }
}

/// Converts one column definition to its snapshot form.
#[must_use]
pub fn convert_column(column: &ColumnDefinition, name: String) -> Column {
    Column {
        attributes: normalized_attributes(column.storage(), column.auto_increment()),
        auto_increment: column.auto_increment(),
        dao_type: dao_type_name(column.physical_type()).to_owned(),
        name,
        ordinal: u64::from(column.ordinal().get()),
        properties: PropertyMap::new(),
        size: Some(normalized_size(
            column.physical_type(),
            u64::from(column.size()),
        )),
    }
}

/// A converted value, or an external long value the caller must stream.
#[derive(Debug)]
pub enum Converted {
    /// The value is complete.
    Value(TypedValue),
    /// The payload lives outside the row.
    External(LongValueReference),
}

/// Converts one decoded field into its typed canonical value.
pub fn convert_value(decoded: &DecodedValue<'_>) -> Result<Converted, SnapshotError> {
    let raw = decoded.raw_bytes().unwrap_or_default();
    let value = match decoded.kind() {
        ValueKind::Null => TypedValue::null(),
        ValueKind::Boolean(value) => TypedValue {
            code_page: None,
            kind: "boolean",
            raw_hex: None,
            value: Scalar::Boolean(*value),
        },
        ValueKind::Byte(value) => {
            TypedValue::with_raw("byte", Scalar::Integer(i64::from(*value)), raw)
        }
        ValueKind::Integer(value) => {
            TypedValue::with_raw("integer", Scalar::Integer(i64::from(*value)), raw)
        }
        ValueKind::Long(value) => {
            TypedValue::with_raw("long", Scalar::Integer(i64::from(*value)), raw)
        }
        ValueKind::Currency(value) => TypedValue::with_raw(
            "currency",
            Scalar::Text(currency_text(value.scaled(), value.scale())),
            raw,
        ),
        ValueKind::Single(value) => TypedValue::with_raw(
            "single",
            Scalar::Number(canonical_number(f64::from(*value))?),
            raw,
        ),
        ValueKind::Double(value) => {
            TypedValue::with_raw("double", Scalar::Number(canonical_number(*value)?), raw)
        }
        ValueKind::DateTime(value) => {
            TypedValue::with_raw("datetime", Scalar::Text(datetime_text(value.days())?), raw)
        }
        ValueKind::Binary(bytes) => TypedValue::with_raw("binary", Scalar::Text(hex(bytes)), raw),
        ValueKind::Text(text) => text_value("text", text),
        ValueKind::Guid(guid) => {
            TypedValue::with_raw("guid", Scalar::Text(guid_text(guid.display_bytes())), raw)
        }
        ValueKind::LongValue(LongValue::Inline { value, .. }) => match value {
            InlineLongValue::Text(text) => text_value("memo", text),
            InlineLongValue::Binary(bytes) => ole_value(bytes),
            _ => return Err(SnapshotError::UnsupportedValue("inline long value form")),
        },
        ValueKind::LongValue(LongValue::External(reference)) => {
            return Ok(Converted::External(*reference));
        }
        _ => return Err(SnapshotError::UnsupportedValue("value kind")),
    };
    Ok(Converted::Value(value))
}

/// A `text` or `memo` value from decoded text and its source bytes.
#[must_use]
pub fn text_value(kind: &'static str, text: &DecodedText<'_>) -> TypedValue {
    TypedValue {
        code_page: Some(u32::from(text.code_page().number())),
        kind,
        raw_hex: Some(hex(text.raw_bytes())),
        value: Scalar::Text(text.as_str().to_owned()),
    }
}

/// A `memo` value assembled from streamed fragments.
#[must_use]
pub fn memo_value(text: String, raw: &[u8], code_page: u16) -> TypedValue {
    TypedValue {
        code_page: Some(u32::from(code_page)),
        kind: "memo",
        raw_hex: Some(hex(raw)),
        value: Scalar::Text(text),
    }
}

/// An `ole` value whose semantic bytes are its payload bytes.
#[must_use]
pub fn ole_value(bytes: &[u8]) -> TypedValue {
    TypedValue::with_raw("ole", Scalar::Text(hex(bytes)), bytes)
}

/// Spells a finite double exactly as Python's `float.__repr__` does.
pub fn canonical_number(value: f64) -> Result<Box<RawValue>, SnapshotError> {
    if !value.is_finite() {
        return Err(SnapshotError::NonFiniteNumber);
    }
    let text = python_repr(value);
    RawValue::from_string(text).map_err(SnapshotError::Json)
}

/// Python `repr` spelling: shortest round-trip digits, fixed notation for
/// exponents in `[-4, 16)`, otherwise `d.ddde±XX`, always with a fraction.
#[must_use]
pub fn python_repr(value: f64) -> String {
    if value == 0.0 {
        return if value.is_sign_negative() {
            "-0.0"
        } else {
            "0.0"
        }
        .to_owned();
    }
    let scientific = format!("{value:e}");
    let (mantissa, exponent) = scientific.split_once('e').unwrap_or((&scientific, "0"));
    let exponent: i32 = exponent.parse().unwrap_or(0);
    let (sign, mantissa) = mantissa
        .strip_prefix('-')
        .map_or(("", mantissa), |unsigned| ("-", unsigned));
    let digits: String = mantissa
        .chars()
        .filter(|character| *character != '.')
        .collect();
    if !(-4..16).contains(&exponent) {
        let exponent_sign = if exponent < 0 { '-' } else { '+' };
        return format!(
            "{sign}{mantissa}e{exponent_sign}{:02}",
            exponent.unsigned_abs()
        );
    }
    let point = exponent + 1;
    let mut output = String::from(sign);
    if point <= 0 {
        output.push_str("0.");
        output.extend(std::iter::repeat_n('0', point.unsigned_abs() as usize));
        output.push_str(&digits);
    } else if point as usize >= digits.len() {
        output.push_str(&digits);
        output.extend(std::iter::repeat_n('0', point as usize - digits.len()));
        output.push_str(".0");
    } else {
        let (integer, fraction) = digits.split_at(point as usize);
        output.push_str(integer);
        output.push('.');
        output.push_str(fraction);
    }
    output
}

/// Renders a scaled currency integer with its fixed decimal scale.
#[must_use]
pub fn currency_text(scaled: i64, scale: u32) -> String {
    let divisor = 10_i128.pow(scale);
    let magnitude = i128::from(scaled).abs();
    let sign = if scaled < 0 { "-" } else { "" };
    let width = scale as usize;
    format!(
        "{sign}{}.{:0width$}",
        magnitude / divisor,
        magnitude % divisor
    )
}

/// Renders display-ordered GUID bytes in lowercase hyphenated form.
#[must_use]
pub fn guid_text(bytes: [u8; 16]) -> String {
    let hex = hex(&bytes);
    format!(
        "{}-{}-{}-{}-{}",
        &hex[0..8],
        &hex[8..12],
        &hex[12..16],
        &hex[16..20],
        &hex[20..32]
    )
}

/// Renders an OLE Automation day count as `YYYY-MM-DDTHH:MM:SS[.fffffffff]`.
///
/// Years are limited to 0100–9999, the fraction is rounded to nanoseconds,
/// a zero fraction is omitted, and trailing fraction zeros are trimmed.
pub fn datetime_text(days: f64) -> Result<String, SnapshotError> {
    const NANOS_PER_DAY: f64 = 86_400_000_000_000.0;
    let invalid = || SnapshotError::DateTimeOutOfRange(days);
    if !days.is_finite() || !(-657_435.0..=2_958_465.999_999_99).contains(&days) {
        return Err(invalid());
    }
    let epoch = days_before_year(1899) + days_before_month(1899, 12) + 29;
    let mut ordinal = epoch + days.trunc() as i64;
    let mut nanoseconds = (days.fract().abs() * NANOS_PER_DAY).round() as u64;
    if nanoseconds == NANOS_PER_DAY as u64 {
        ordinal += 1;
        nanoseconds = 0;
    }
    let (year, month, day) = date_from_ordinal(ordinal).ok_or_else(invalid)?;
    if year < 100 {
        return Err(invalid());
    }
    let hour = nanoseconds / 3_600_000_000_000;
    let minute = nanoseconds % 3_600_000_000_000 / 60_000_000_000;
    let second = nanoseconds % 60_000_000_000 / 1_000_000_000;
    let fraction = nanoseconds % 1_000_000_000;
    let mut text = format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}");
    if fraction != 0 {
        text.push('.');
        text.push_str(format!("{fraction:09}").trim_end_matches('0'));
    }
    Ok(text)
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
    let day = u8::try_from(day_of_year - days_before_month(low, month) + 1).ok()?;
    Some((low, month, day))
}

#[cfg(test)]
mod tests {
    use super::{
        currency_text, datetime_text, guid_text, normalized_attributes, normalized_size,
        python_repr,
    };
    use jet3::{ColumnPhysicalType, ColumnStorageClass};

    type TestResult = Result<(), Box<dyn std::error::Error>>;

    fn vector_rows(text: &'static str) -> impl Iterator<Item = Vec<&'static str>> {
        text.lines()
            .filter(|line| !line.starts_with('#') && !line.is_empty())
            .map(|line| line.split('\t').collect())
    }

    #[test]
    fn datetime_spelling_matches_the_shared_vectors() -> TestResult {
        let vectors = include_str!(
            "../../../oracle/windows-dao/protocol/v1_2/fixtures/canonical-datetime-vectors.tsv"
        );
        let mut seen = 0;
        for fields in vector_rows(vectors) {
            let [case, raw_bits, expected, ..] = fields.as_slice() else {
                return Err(format!("malformed datetime vector {fields:?}").into());
            };
            if *raw_bits == "-" {
                continue;
            }
            let days = f64::from_bits(u64::from_str_radix(raw_bits, 16)?);
            let actual = datetime_text(days).ok();
            let expected = (*expected != "reject").then(|| (*expected).to_owned());
            assert_eq!(actual, expected, "{case}");
            seen += 1;
        }
        assert_eq!(seen, 14);
        Ok(())
    }

    #[test]
    fn float_spelling_matches_the_shared_vectors() -> TestResult {
        let vectors = include_str!(
            "../../../oracle/windows-dao/protocol/v1_2/fixtures/canonical-float-vectors.tsv"
        );
        let mut seen = 0;
        for fields in vector_rows(vectors) {
            let [case, kind, bits, _, expected, ..] = fields.as_slice() else {
                return Err(format!("malformed float vector {fields:?}").into());
            };
            let value = match *kind {
                "single" => f64::from(f32::from_bits(u32::from_str_radix(bits, 16)?)),
                _ => f64::from_bits(u64::from_str_radix(bits, 16)?),
            };
            assert_eq!(python_repr(value), *expected, "{case}");
            seen += 1;
        }
        assert_eq!(seen, 18);
        Ok(())
    }

    #[test]
    fn column_normalization_matches_the_shared_vectors() -> TestResult {
        let vectors = include_str!(
            "../../../oracle/windows-dao/protocol/v1_2/fixtures/column-normalization-vectors.tsv"
        );
        let mut seen = 0;
        for fields in vector_rows(vectors) {
            let [
                dao_type,
                declared,
                storage,
                auto_increment,
                size,
                attributes,
            ] = fields.as_slice()
            else {
                return Err(format!("malformed column vector {fields:?}").into());
            };
            let physical = [
                ColumnPhysicalType::Boolean,
                ColumnPhysicalType::Byte,
                ColumnPhysicalType::Integer,
                ColumnPhysicalType::Long,
                ColumnPhysicalType::Currency,
                ColumnPhysicalType::Single,
                ColumnPhysicalType::Double,
                ColumnPhysicalType::DateTime,
                ColumnPhysicalType::Binary,
                ColumnPhysicalType::Text,
                ColumnPhysicalType::LongBinary,
                ColumnPhysicalType::Memo,
                ColumnPhysicalType::Guid,
            ]
            .into_iter()
            .find(|candidate| super::dao_type_name(*candidate) == *dao_type)
            .ok_or("unknown DAO type")?;
            let storage = if *storage == "fixed" {
                ColumnStorageClass::Fixed { offset: 0 }
            } else {
                ColumnStorageClass::Variable { index: 0 }
            };
            assert_eq!(
                normalized_size(physical, declared.parse()?),
                size.parse::<u64>()?
            );
            assert_eq!(
                normalized_attributes(storage, auto_increment.parse()?),
                attributes.parse::<i64>()?
            );
            seen += 1;
        }
        assert_eq!(seen, 15);
        Ok(())
    }

    #[test]
    fn currency_and_guid_spellings_are_invariant() {
        assert_eq!(currency_text(-15_000, 4), "-1.5000");
        assert_eq!(currency_text(0, 4), "0.0000");
        assert_eq!(currency_text(i64::MAX, 4), "922337203685477.5807");
        assert_eq!(
            guid_text([
                0x12, 0x34, 0x56, 0x78, 0x9a, 0xbc, 0xde, 0xf0, 1, 2, 3, 4, 5, 6, 7, 8
            ]),
            "12345678-9abc-def0-0102-030405060708"
        );
    }
}
