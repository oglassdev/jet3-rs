//! Checked encoder for one logical Jet 3 data row, the inverse of the layout
//! validated by `row.rs` (`EXP-0060`) with the scalar encodings of `EXP-0061`.
//!
//! Memo and OLE values are supplied as already-encoded long-value bytes
//! (12-byte header plus any inline payload); writing external LVAL pages is a
//! separate concern.

use std::fmt;

use crate::{
    BinaryWriter, ByteCount, ByteOffset, ColumnDefinition, ColumnPhysicalType, ColumnStorageClass,
    Error, ResourceBudget,
};

/// `EXP-0060`: one-byte column count, so at most 255 columns.
const MAX_COLUMN_COUNT: usize = u8::MAX as usize;
/// `EXP-0060`: rows longer than 255 bytes use one jump byte for boundary
/// high bits; only single-variable-column wide rows were isolated.
const MAX_NARROW_ROW_LEN: usize = u8::MAX as usize;
/// `EXP-0060`: a jump bit adds 256 to a boundary, so boundaries stay below 512.
const MAX_WIDE_BOUNDARY: usize = 2 * (u8::MAX as usize + 1) - 1;
/// `EXP-0061`: every long field begins with a 12-byte header.
const LONG_VALUE_HEADER_LEN: usize = 12;
const NULL_MAP_MAX_LEN: usize = MAX_COLUMN_COUNT.div_ceil(8);

/// Row placement of one column, independent of how the schema was obtained.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RowColumnLayout {
    physical_type: ColumnPhysicalType,
    storage: ColumnStorageClass,
    size: u16,
}

impl RowColumnLayout {
    /// Describes a column by type, resolved storage, and declared size.
    #[must_use]
    pub const fn new(
        physical_type: ColumnPhysicalType,
        storage: ColumnStorageClass,
        size: u16,
    ) -> Self {
        Self {
            physical_type,
            storage,
            size,
        }
    }

    /// Returns the physical type.
    #[must_use]
    pub const fn physical_type(self) -> ColumnPhysicalType {
        self.physical_type
    }

    /// Returns the resolved storage class.
    #[must_use]
    pub const fn storage(self) -> ColumnStorageClass {
        self.storage
    }

    /// Returns the declared fixed or maximum size.
    #[must_use]
    pub const fn size(self) -> u16 {
        self.size
    }
}

impl From<&ColumnDefinition> for RowColumnLayout {
    fn from(column: &ColumnDefinition) -> Self {
        Self::new(column.physical_type(), column.storage(), column.size())
    }
}

/// One value to encode, in the physical shapes established by `EXP-0061`.
#[derive(Debug, Clone, Copy, PartialEq)]
#[non_exhaustive]
pub enum RowValue<'a> {
    /// Absent value; for Boolean columns equivalent to `false`.
    Null,
    /// Boolean stored as the presence bit.
    Boolean(bool),
    /// Unsigned eight-bit integer.
    Byte(u8),
    /// Signed 16-bit integer.
    Integer(i16),
    /// Signed 32-bit integer.
    Long(i32),
    /// Currency as its integer scaled by 10,000.
    Currency {
        /// Stored integer before applying the four-place scale.
        scaled: i64,
    },
    /// IEEE-754 single-precision value.
    Single(f32),
    /// IEEE-754 double-precision value.
    Double(f64),
    /// OLE Automation day count.
    DateTime {
        /// Day count as stored.
        days: f64,
    },
    /// Short binary bytes.
    Binary(&'a [u8]),
    /// Text already encoded in the database code page.
    Text(&'a [u8]),
    /// Replication ID in conventional display-byte order.
    Guid([u8; 16]),
    /// Already-encoded Memo/OLE header plus inline payload.
    LongValue(&'a [u8]),
}

/// Structured failure while validating or encoding a row.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum RowWriteError {
    /// More columns than the one-byte row count can hold.
    TooManyColumns {
        /// Requested column count.
        count: usize,
        /// Maximum encodable count.
        maximum: usize,
    },
    /// The value slice length differs from the column count.
    ValueCountMismatch {
        /// Column count.
        expected: usize,
        /// Value count.
        actual: usize,
    },
    /// A value variant does not match the column's physical type.
    TypeMismatch {
        /// Zero-based column ordinal.
        ordinal: u16,
        /// Column physical type.
        physical_type: ColumnPhysicalType,
    },
    /// A value's byte length is incompatible with the column.
    InvalidWidth {
        /// Zero-based column ordinal.
        ordinal: u16,
        /// Column physical type.
        physical_type: ColumnPhysicalType,
        /// Required or maximum byte length.
        expected: usize,
        /// Provided byte length.
        actual: usize,
    },
    /// A column's storage class is incompatible with its physical type.
    InvalidStorage {
        /// Zero-based column ordinal.
        ordinal: u16,
        /// Column physical type.
        physical_type: ColumnPhysicalType,
    },
    /// Variable indexes are not exactly `0..variable_count`.
    InvalidVariableIndex {
        /// Zero-based column ordinal.
        ordinal: u16,
        /// Requested variable index.
        index: u16,
        /// Variable column count.
        variable_count: usize,
    },
    /// The row needs the unobserved multi-column wide offset encoding.
    UnsupportedWideVariableOffsets {
        /// Variable column count.
        variable_count: usize,
        /// Complete row length.
        row_length: usize,
    },
    /// A variable boundary exceeds what the jump byte can represent.
    BoundaryTooLarge {
        /// Zero-based variable index whose end boundary overflowed.
        index: u16,
        /// Requested boundary.
        boundary: usize,
        /// Maximum boundary.
        maximum: usize,
    },
    /// The output slice cannot hold the complete row.
    OutputTooSmall {
        /// Required length.
        needed: usize,
        /// Provided length.
        available: usize,
    },
    /// Resource policy or checked arithmetic rejected the encoding.
    Resource(Error),
}

impl fmt::Display for RowWriteError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "row encoding failed: {self:?}")
    }
}

impl std::error::Error for RowWriteError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Resource(source) => Some(source),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Copy)]
struct RowShape {
    fixed_size: usize,
    variable_count: usize,
    null_len: usize,
    wide: bool,
    length: usize,
}

/// Encodes one row into `output`, returning the encoded length.
pub fn encode_row(
    columns: &[RowColumnLayout],
    values: &[RowValue<'_>],
    output: &mut [u8],
    budget: &mut ResourceBudget,
) -> Result<ByteCount, RowWriteError> {
    let shape = validate(columns, values, budget)?;
    if output.len() < shape.length {
        return Err(RowWriteError::OutputTooSmall {
            needed: shape.length,
            available: output.len(),
        });
    }
    let mut writer = BinaryWriter::new(output, budget).map_err(RowWriteError::Resource)?;
    write_row(&mut writer, columns, values, shape).map_err(RowWriteError::Resource)?;
    Ok(ByteCount::new(writer.position().get()))
}

fn validate(
    columns: &[RowColumnLayout],
    values: &[RowValue<'_>],
    budget: &mut ResourceBudget,
) -> Result<RowShape, RowWriteError> {
    if columns.len() > MAX_COLUMN_COUNT {
        return Err(RowWriteError::TooManyColumns {
            count: columns.len(),
            maximum: MAX_COLUMN_COUNT,
        });
    }
    if values.len() != columns.len() {
        return Err(RowWriteError::ValueCountMismatch {
            expected: columns.len(),
            actual: values.len(),
        });
    }
    budget
        .charge_items(columns.len() as u64)
        .map_err(RowWriteError::Resource)?;
    let mut fixed_size = 0_usize;
    let mut variable_count = 0_usize;
    let mut variable_bytes = 0_usize;
    let mut seen_indexes = [false; MAX_COLUMN_COUNT];
    for (ordinal, (column, value)) in (0_u16..).zip(columns.iter().zip(values)) {
        let width = value_width(ordinal, *column, *value)?;
        match column.storage {
            ColumnStorageClass::Fixed { offset } => {
                if column.physical_type != ColumnPhysicalType::Boolean {
                    fixed_size = fixed_size.max(usize::from(offset) + usize::from(column.size));
                }
            }
            ColumnStorageClass::Variable { index } => {
                let slot = seen_indexes
                    .get_mut(usize::from(index))
                    .filter(|seen| !**seen);
                let Some(slot) = slot else {
                    return Err(RowWriteError::InvalidVariableIndex {
                        ordinal,
                        index,
                        variable_count: columns.len(),
                    });
                };
                *slot = true;
                variable_count += 1;
                variable_bytes += width;
            }
        }
    }
    // Indexes are unique, so any index at or beyond the count leaves a hole.
    for (ordinal, column) in (0_u16..).zip(columns) {
        if let ColumnStorageClass::Variable { index } = column.storage
            && usize::from(index) >= variable_count
        {
            return Err(RowWriteError::InvalidVariableIndex {
                ordinal,
                index,
                variable_count,
            });
        }
    }
    let null_len = columns.len().div_ceil(8);
    let mut length = 1 + fixed_size + variable_bytes + null_len;
    if variable_count > 0 {
        length += variable_count + 1 + 1;
    }
    let wide = length > MAX_NARROW_ROW_LEN && variable_count > 0;
    if wide {
        if variable_count != 1 {
            return Err(RowWriteError::UnsupportedWideVariableOffsets {
                variable_count,
                row_length: length,
            });
        }
        length += 1;
        let last_boundary = 1 + fixed_size + variable_bytes;
        if last_boundary > MAX_WIDE_BOUNDARY {
            return Err(RowWriteError::BoundaryTooLarge {
                index: 0,
                boundary: last_boundary,
                maximum: MAX_WIDE_BOUNDARY,
            });
        }
    }
    Ok(RowShape {
        fixed_size,
        variable_count,
        null_len,
        wide,
        length,
    })
}

/// Checks the value against the column and returns its physical byte width.
fn value_width(
    ordinal: u16,
    column: RowColumnLayout,
    value: RowValue<'_>,
) -> Result<usize, RowWriteError> {
    let physical_type = column.physical_type;
    let fixed = matches!(column.storage, ColumnStorageClass::Fixed { .. });
    let storage_error = RowWriteError::InvalidStorage {
        ordinal,
        physical_type,
    };
    let mismatch = RowWriteError::TypeMismatch {
        ordinal,
        physical_type,
    };
    let width = |actual: usize, expected: usize, exact: bool| {
        if (exact && actual != expected) || (!exact && actual > expected) {
            Err(RowWriteError::InvalidWidth {
                ordinal,
                physical_type,
                expected,
                actual,
            })
        } else {
            Ok(actual)
        }
    };
    let size = usize::from(column.size);
    match physical_type {
        ColumnPhysicalType::Boolean => match (fixed, value) {
            (true, RowValue::Null | RowValue::Boolean(_)) => Ok(0),
            (false, _) => Err(storage_error),
            _ => Err(mismatch),
        },
        ColumnPhysicalType::Byte
        | ColumnPhysicalType::Integer
        | ColumnPhysicalType::Long
        | ColumnPhysicalType::Currency
        | ColumnPhysicalType::Single
        | ColumnPhysicalType::Double
        | ColumnPhysicalType::DateTime
        | ColumnPhysicalType::Guid => {
            if !fixed {
                return Err(storage_error);
            }
            let matches = match value {
                RowValue::Null => true,
                RowValue::Byte(_) => physical_type == ColumnPhysicalType::Byte,
                RowValue::Integer(_) => physical_type == ColumnPhysicalType::Integer,
                RowValue::Long(_) => physical_type == ColumnPhysicalType::Long,
                RowValue::Currency { .. } => physical_type == ColumnPhysicalType::Currency,
                RowValue::Single(_) => physical_type == ColumnPhysicalType::Single,
                RowValue::Double(_) => physical_type == ColumnPhysicalType::Double,
                RowValue::DateTime { .. } => physical_type == ColumnPhysicalType::DateTime,
                RowValue::Guid(_) => physical_type == ColumnPhysicalType::Guid,
                _ => false,
            };
            if matches { Ok(size) } else { Err(mismatch) }
        }
        ColumnPhysicalType::Text => match value {
            RowValue::Null => Ok(0),
            RowValue::Text(bytes) => width(bytes.len(), size, fixed),
            _ => Err(mismatch),
        },
        ColumnPhysicalType::Binary => match (fixed, value) {
            (true, _) => Err(storage_error),
            (false, RowValue::Null) => Ok(0),
            (false, RowValue::Binary(bytes)) => width(bytes.len(), size, false),
            _ => Err(mismatch),
        },
        ColumnPhysicalType::LongBinary | ColumnPhysicalType::Memo => match (fixed, value) {
            (true, _) => Err(storage_error),
            (false, RowValue::Null) => Ok(0),
            (false, RowValue::LongValue(bytes)) => {
                if bytes.len() < LONG_VALUE_HEADER_LEN {
                    return Err(RowWriteError::InvalidWidth {
                        ordinal,
                        physical_type,
                        expected: LONG_VALUE_HEADER_LEN,
                        actual: bytes.len(),
                    });
                }
                Ok(bytes.len())
            }
            _ => Err(mismatch),
        },
    }
}

fn write_row(
    writer: &mut BinaryWriter<'_, '_>,
    columns: &[RowColumnLayout],
    values: &[RowValue<'_>],
    shape: RowShape,
) -> Result<(), Error> {
    let count = u8::try_from(columns.len()).map_err(|_| Error::IntegerConversion {
        value: columns.len() as u128,
        target: "u8",
    })?;
    writer.write_u8(count)?;
    // EXP-0060: bytes reserved for null fixed fields are not interpreted.
    write_zeros(writer, shape.fixed_size)?;
    let fixed_end = writer.position();
    let mut null_map = [0_u8; NULL_MAP_MAX_LEN];
    for (ordinal, (column, value)) in columns.iter().zip(values).enumerate() {
        let present = match (column.physical_type, value) {
            (ColumnPhysicalType::Boolean, RowValue::Boolean(bit)) => *bit,
            (_, RowValue::Null) => false,
            (ColumnPhysicalType::Boolean, _) => false,
            _ => true,
        };
        if present {
            null_map[ordinal / 8] |= 1 << (ordinal % 8);
        }
        if let (ColumnStorageClass::Fixed { offset }, true) = (column.storage, present) {
            if column.physical_type == ColumnPhysicalType::Boolean {
                continue;
            }
            let position = ByteOffset::new(1).checked_add(ByteCount::new(u64::from(offset)))?;
            writer.seek(position)?;
            write_scalar(writer, *value)?;
        }
    }
    writer.seek(fixed_end)?;
    let mut boundaries = [0_usize; MAX_COLUMN_COUNT + 1];
    boundaries[0] = fixed_end.to_usize()?;
    for index in 0..shape.variable_count {
        let Some((_, value)) = columns.iter().zip(values).find(|(column, _)| {
            column.storage
                == ColumnStorageClass::Variable {
                    index: index as u16,
                }
        }) else {
            return Err(Error::Arithmetic {
                operation: "locate validated variable column",
            });
        };
        match value {
            RowValue::Text(bytes) | RowValue::Binary(bytes) | RowValue::LongValue(bytes) => {
                writer.write_exact(bytes)?;
            }
            _ => {}
        }
        boundaries[index + 1] = writer.position().to_usize()?;
    }
    if shape.variable_count > 0 {
        // EXP-0060: boundaries in reverse order, low byte each.
        let mut jump = 0_u8;
        for ordinal in (0..=shape.variable_count).rev() {
            let boundary = boundaries[ordinal];
            writer.write_u8((boundary & 0xff) as u8)?;
            let reversed = shape.variable_count - ordinal;
            if shape.wide && boundary & 0x100 != 0 {
                jump |= 1 << reversed;
            }
        }
        if shape.wide {
            writer.write_u8(jump)?;
        }
        writer.write_u8(shape.variable_count as u8)?;
    }
    writer.write_exact(&null_map[..shape.null_len])
}

fn write_scalar(writer: &mut BinaryWriter<'_, '_>, value: RowValue<'_>) -> Result<(), Error> {
    match value {
        RowValue::Byte(raw) => writer.write_u8(raw),
        RowValue::Integer(raw) => writer.write_i16_le(raw),
        RowValue::Long(raw) => writer.write_i32_le(raw),
        RowValue::Currency { scaled } => writer.write_i64_le(scaled),
        RowValue::Single(raw) => writer.write_f32_le(raw),
        RowValue::Double(raw) | RowValue::DateTime { days: raw } => writer.write_f64_le(raw),
        RowValue::Text(bytes) => writer.write_exact(bytes),
        // EXP-0061: first three groups little-endian, last eight bytes in display order.
        RowValue::Guid(d) => writer.write_exact(&[
            d[3], d[2], d[1], d[0], d[5], d[4], d[7], d[6], d[8], d[9], d[10], d[11], d[12], d[13],
            d[14], d[15],
        ]),
        RowValue::Null | RowValue::Boolean(_) | RowValue::Binary(_) | RowValue::LongValue(_) => {
            Ok(())
        }
    }
}

fn write_zeros(writer: &mut BinaryWriter<'_, '_>, mut count: usize) -> Result<(), Error> {
    const ZEROS: [u8; 64] = [0; 64];
    while count > 0 {
        let chunk = count.min(ZEROS.len());
        writer.write_exact(&ZEROS[..chunk])?;
        count -= chunk;
    }
    Ok(())
}

#[cfg(test)]
#[path = "row_writer_tests.rs"]
mod tests;
