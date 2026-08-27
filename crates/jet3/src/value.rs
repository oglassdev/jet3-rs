//! Lossless scalar and short-value decoding from `EXP-0061`.

use std::fmt;
use std::mem::size_of;

use crate::long_value::{LongValue, LongValueError, LongValueKind};
use crate::text::{DecodedText, TextCodePage, TextError, decode_text};
use crate::{ByteCount, ColumnPhysicalType, Error, RawField, ResourceBudget, RowLocator};

/// A Currency value stored as an integer scaled by exactly 10,000.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CurrencyValue {
    scaled: i64,
}

impl CurrencyValue {
    #[must_use]
    pub const fn scaled(self) -> i64 {
        self.scaled
    }

    #[must_use]
    pub const fn scale(self) -> u32 {
        4
    }
}

/// An OLE Automation date represented as its exact sourced day count.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct DateTimeValue {
    days: f64,
}

impl DateTimeValue {
    #[must_use]
    pub const fn days(self) -> f64 {
        self.days
    }
}

/// A Replication ID in conventional display-byte order.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct GuidValue {
    display_bytes: [u8; 16],
}

impl GuidValue {
    #[must_use]
    pub const fn display_bytes(self) -> [u8; 16] {
        self.display_bytes
    }
}

/// The interpreted portion of one value.
#[derive(Debug, PartialEq)]
#[non_exhaustive]
pub enum ValueKind<'raw> {
    Null,
    Boolean(bool),
    Byte(u8),
    Integer(i16),
    Long(i32),
    Currency(CurrencyValue),
    Single(f32),
    Double(f64),
    DateTime(DateTimeValue),
    Binary(&'raw [u8]),
    Text(DecodedText<'raw>),
    Guid(GuidValue),
    LongValue(LongValue<'raw>),
}

/// One typed value retaining its exact physical bytes whenever present.
#[derive(Debug, PartialEq)]
pub struct DecodedValue<'raw> {
    raw: Option<&'raw [u8]>,
    kind: ValueKind<'raw>,
}

impl<'raw> DecodedValue<'raw> {
    #[must_use]
    pub const fn raw_bytes(&self) -> Option<&'raw [u8]> {
        self.raw
    }

    #[must_use]
    pub const fn kind(&self) -> &ValueKind<'raw> {
        &self.kind
    }
}

/// A scalar, text, or long-value header failure.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum ValueError {
    InvalidWidth {
        physical_type: ColumnPhysicalType,
        expected: usize,
        actual: usize,
    },
    Text(TextError),
    LongValue(LongValueError),
    Resource(Error),
}

impl fmt::Display for ValueError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "value decoding failed: {self:?}")
    }
}

impl std::error::Error for ValueError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Text(source) => Some(source),
            Self::LongValue(source) => Some(source),
            Self::Resource(source) => Some(source),
            Self::InvalidWidth { .. } => None,
        }
    }
}

pub(crate) fn decode_value<'raw>(
    physical_type: ColumnPhysicalType,
    field: RawField<'raw>,
    boolean_bit: bool,
    source: RowLocator,
    code_page: TextCodePage,
    budget: &mut ResourceBudget,
) -> Result<DecodedValue<'raw>, ValueError> {
    let RawField::Bytes(raw) = field else {
        budget
            .charge_decoded_value(ByteCount::new(0))
            .map_err(ValueError::Resource)?;
        return Ok(DecodedValue {
            raw: None,
            kind: ValueKind::Null,
        });
    };
    let kind = match physical_type {
        ColumnPhysicalType::Boolean => {
            expect_width(physical_type, raw, 0)?;
            charge_output::<bool>(budget)?;
            ValueKind::Boolean(boolean_bit)
        }
        ColumnPhysicalType::Byte => {
            expect_width(physical_type, raw, 1)?;
            charge_output::<u8>(budget)?;
            ValueKind::Byte(raw[0])
        }
        ColumnPhysicalType::Integer => {
            charge_output::<i16>(budget)?;
            ValueKind::Integer(i16::from_le_bytes(take_array(physical_type, raw)?))
        }
        ColumnPhysicalType::Long => {
            charge_output::<i32>(budget)?;
            ValueKind::Long(i32::from_le_bytes(take_array(physical_type, raw)?))
        }
        ColumnPhysicalType::Currency => {
            charge_output::<i64>(budget)?;
            ValueKind::Currency(CurrencyValue {
                scaled: i64::from_le_bytes(take_array(physical_type, raw)?),
            })
        }
        ColumnPhysicalType::Single => {
            charge_output::<f32>(budget)?;
            ValueKind::Single(f32::from_bits(u32::from_le_bytes(take_array(
                physical_type,
                raw,
            )?)))
        }
        ColumnPhysicalType::Double => {
            charge_output::<f64>(budget)?;
            ValueKind::Double(f64::from_bits(u64::from_le_bytes(take_array(
                physical_type,
                raw,
            )?)))
        }
        ColumnPhysicalType::DateTime => {
            charge_output::<f64>(budget)?;
            ValueKind::DateTime(DateTimeValue {
                days: f64::from_bits(u64::from_le_bytes(take_array(physical_type, raw)?)),
            })
        }
        ColumnPhysicalType::Binary => {
            budget
                .charge_decoded_value(
                    ByteCount::from_usize(raw.len()).map_err(ValueError::Resource)?,
                )
                .map_err(ValueError::Resource)?;
            ValueKind::Binary(raw)
        }
        ColumnPhysicalType::Text => {
            ValueKind::Text(decode_text(raw, code_page, budget).map_err(ValueError::Text)?)
        }
        ColumnPhysicalType::Guid => {
            let raw: [u8; 16] = take_array(physical_type, raw)?;
            charge_output::<GuidValue>(budget)?;
            ValueKind::Guid(GuidValue {
                display_bytes: [
                    raw[3], raw[2], raw[1], raw[0], raw[5], raw[4], raw[7], raw[6], raw[8], raw[9],
                    raw[10], raw[11], raw[12], raw[13], raw[14], raw[15],
                ],
            })
        }
        ColumnPhysicalType::LongBinary => ValueKind::LongValue(
            LongValue::decode(raw, source, LongValueKind::Ole, code_page, budget)
                .map_err(ValueError::LongValue)?,
        ),
        ColumnPhysicalType::Memo => ValueKind::LongValue(
            LongValue::decode(raw, source, LongValueKind::Memo, code_page, budget)
                .map_err(ValueError::LongValue)?,
        ),
    };
    Ok(DecodedValue {
        raw: Some(raw),
        kind,
    })
}

fn expect_width(
    physical_type: ColumnPhysicalType,
    raw: &[u8],
    expected: usize,
) -> Result<(), ValueError> {
    if raw.len() != expected {
        return Err(ValueError::InvalidWidth {
            physical_type,
            expected,
            actual: raw.len(),
        });
    }
    Ok(())
}

fn take_array<const N: usize>(
    physical_type: ColumnPhysicalType,
    raw: &[u8],
) -> Result<[u8; N], ValueError> {
    expect_width(physical_type, raw, N)?;
    raw.try_into().map_err(|_| ValueError::InvalidWidth {
        physical_type,
        expected: N,
        actual: raw.len(),
    })
}

fn charge_output<T>(budget: &mut ResourceBudget) -> Result<(), ValueError> {
    budget
        .charge_decoded_value(ByteCount::new(size_of::<T>() as u64))
        .map_err(ValueError::Resource)
}

#[cfg(test)]
#[path = "value_tests.rs"]
mod tests;
