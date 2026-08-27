//! Lossless Jet 3 column definitions and database-code-page names.
//!
//! `SRC-0023` supplies the checked DAO candidate inventory and `EXP-0059`
//! supplies the physical type, size, class, ordinal, and name observations.

use std::mem::size_of;

use crate::definition_name::{DefinitionName, contains_name};
use crate::table_definition::TableDefinitionError;
use crate::{ByteCount, Error, ResourceBudget};

pub(crate) const COLUMN_RECORD_LEN: usize = 18;
const VARIABLE_CLASS: u8 = 2;
const FIXED_CLASS: u8 = 3;
const AUTO_INCREMENT_CLASS: u8 = 7;

/// A zero-based table-column ordinal.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct ColumnOrdinal(u16);

impl ColumnOrdinal {
    pub(crate) const fn new(value: u16) -> Self {
        Self(value)
    }

    #[must_use]
    pub const fn get(self) -> u16 {
        self.0
    }
}

/// Closed Jet 3 physical type inventory admitted by the checked DAO provider.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ColumnPhysicalType {
    Boolean,
    Byte,
    Integer,
    Long,
    Currency,
    Single,
    Double,
    DateTime,
    Binary,
    Text,
    LongBinary,
    Memo,
    Guid,
}

impl ColumnPhysicalType {
    #[must_use]
    pub const fn raw(self) -> u8 {
        match self {
            Self::Boolean => 1,
            Self::Byte => 2,
            Self::Integer => 3,
            Self::Long => 4,
            Self::Currency => 5,
            Self::Single => 6,
            Self::Double => 7,
            Self::DateTime => 8,
            Self::Binary => 9,
            Self::Text => 10,
            Self::LongBinary => 11,
            Self::Memo => 12,
            Self::Guid => 15,
        }
    }
}

/// Physical fixed/variable storage class for one column.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ColumnStorageClass {
    Fixed { offset: u16 },
    Variable { index: u16 },
}

/// One immutable, lossless column definition.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ColumnDefinition {
    name: DefinitionName,
    ordinal: ColumnOrdinal,
    physical_type: ColumnPhysicalType,
    storage: ColumnStorageClass,
    size: u16,
    auto_increment: bool,
    raw_variable_counter: u16,
    sourced_constant: u16,
    raw_encoding_context: [u8; 4],
    raw_class_flags: u8,
    raw_record: [u8; COLUMN_RECORD_LEN],
}

impl ColumnDefinition {
    #[must_use]
    pub const fn name(&self) -> &DefinitionName {
        &self.name
    }
    #[must_use]
    pub const fn ordinal(&self) -> ColumnOrdinal {
        self.ordinal
    }
    #[must_use]
    pub const fn physical_type(&self) -> ColumnPhysicalType {
        self.physical_type
    }
    #[must_use]
    pub const fn storage(&self) -> ColumnStorageClass {
        self.storage
    }
    #[must_use]
    pub const fn size(&self) -> u16 {
        self.size
    }
    #[must_use]
    pub const fn auto_increment(&self) -> bool {
        self.auto_increment
    }
    #[must_use]
    pub const fn raw_variable_counter(&self) -> u16 {
        self.raw_variable_counter
    }
    #[must_use]
    pub const fn sourced_constant(&self) -> u16 {
        self.sourced_constant
    }
    #[must_use]
    pub const fn raw_encoding_context(&self) -> &[u8; 4] {
        &self.raw_encoding_context
    }
    #[must_use]
    pub const fn raw_class_flags(&self) -> u8 {
        self.raw_class_flags
    }
    #[must_use]
    pub const fn raw_record(&self) -> &[u8; COLUMN_RECORD_LEN] {
        &self.raw_record
    }
}

pub(crate) fn decode_columns(
    bytes: &[u8],
    offset: &mut usize,
    column_count: u16,
    variable_count: u16,
    budget: &mut ResourceBudget,
) -> Result<Vec<ColumnDefinition>, TableDefinitionError> {
    budget
        .charge_items(u64::from(column_count).saturating_mul(2))
        .map_err(TableDefinitionError::Resource)?;
    charge_vec::<RawColumn>(column_count, budget)?;
    charge_vec::<ColumnDefinition>(column_count, budget)?;
    let mut raw_columns = Vec::new();
    raw_columns
        .try_reserve_exact(usize::from(column_count))
        .map_err(|_| allocation_failure("reserve raw column definitions"))?;
    let mut variables_seen = 0_u16;
    let mut next_fixed_offset = 0_u16;
    for record_ordinal in 0..column_count {
        let raw_record = take_array::<COLUMN_RECORD_LEN>(bytes, offset)?;
        let first_ordinal = u16_at(&raw_record, 1);
        let variable_counter = u16_at(&raw_record, 3);
        let repeated_ordinal = u16_at(&raw_record, 5);
        if first_ordinal != record_ordinal || repeated_ordinal != record_ordinal {
            return Err(TableDefinitionError::InvalidColumnOrdinal {
                record: record_ordinal,
                first: first_ordinal,
                repeated: repeated_ordinal,
            });
        }
        if variable_counter != variables_seen {
            return Err(TableDefinitionError::InvalidVariableCounter {
                ordinal: record_ordinal,
                raw: variable_counter,
                expected: variables_seen,
            });
        }
        let sourced_constant = u16_at(&raw_record, 7);
        if sourced_constant != 1 {
            return Err(TableDefinitionError::InvalidColumnConstant {
                ordinal: record_ordinal,
                raw: sourced_constant,
            });
        }
        let encoding_context = array_at::<4>(&raw_record, 9)?;
        if encoding_context != [0x09, 0x04, 0xe4, 0x04] {
            return Err(TableDefinitionError::InvalidColumnEncodingContext {
                ordinal: record_ordinal,
                raw: encoding_context,
            });
        }
        let physical_type = decode_type(record_ordinal, raw_record[0])?;
        let size = u16_at(&raw_record, 16);
        validate_size(record_ordinal, physical_type, size)?;
        let raw_class = raw_record[13];
        let storage = match raw_class {
            VARIABLE_CLASS => {
                if !matches!(
                    physical_type,
                    ColumnPhysicalType::Binary
                        | ColumnPhysicalType::Text
                        | ColumnPhysicalType::LongBinary
                        | ColumnPhysicalType::Memo
                ) {
                    return Err(TableDefinitionError::UnsupportedColumnClass {
                        ordinal: record_ordinal,
                        physical_type,
                        raw: raw_class,
                    });
                }
                let index = variables_seen;
                variables_seen =
                    variables_seen
                        .checked_add(1)
                        .ok_or(TableDefinitionError::Resource(Error::Arithmetic {
                            operation: "advance variable column count",
                        }))?;
                ColumnStorageClass::Variable { index }
            }
            FIXED_CLASS | AUTO_INCREMENT_CLASS => {
                if raw_class == AUTO_INCREMENT_CLASS && physical_type != ColumnPhysicalType::Long {
                    return Err(TableDefinitionError::UnsupportedColumnClass {
                        ordinal: record_ordinal,
                        physical_type,
                        raw: raw_class,
                    });
                }
                if matches!(
                    physical_type,
                    ColumnPhysicalType::Binary
                        | ColumnPhysicalType::LongBinary
                        | ColumnPhysicalType::Memo
                ) {
                    return Err(TableDefinitionError::UnsupportedColumnClass {
                        ordinal: record_ordinal,
                        physical_type,
                        raw: raw_class,
                    });
                }
                let fixed_offset = u16_at(&raw_record, 14);
                if fixed_offset != next_fixed_offset {
                    return Err(TableDefinitionError::InvalidFixedOffset {
                        ordinal: record_ordinal,
                        raw: fixed_offset,
                        expected: next_fixed_offset,
                    });
                }
                if physical_type != ColumnPhysicalType::Boolean {
                    next_fixed_offset = next_fixed_offset.checked_add(size).ok_or(
                        TableDefinitionError::Resource(Error::Arithmetic {
                            operation: "advance fixed column offset",
                        }),
                    )?;
                }
                ColumnStorageClass::Fixed {
                    offset: fixed_offset,
                }
            }
            _ => {
                return Err(TableDefinitionError::UnsupportedColumnClass {
                    ordinal: record_ordinal,
                    physical_type,
                    raw: raw_class,
                });
            }
        };
        raw_columns.push(RawColumn {
            ordinal: ColumnOrdinal::new(record_ordinal),
            physical_type,
            storage,
            size,
            auto_increment: raw_class == AUTO_INCREMENT_CLASS,
            raw_variable_counter: variable_counter,
            sourced_constant,
            raw_encoding_context: encoding_context,
            raw_class_flags: raw_class,
            raw_record,
        });
    }
    if variables_seen != variable_count {
        return Err(TableDefinitionError::InconsistentVariableCount {
            header: variable_count,
            decoded: variables_seen,
        });
    }
    let mut columns = Vec::new();
    columns
        .try_reserve_exact(usize::from(column_count))
        .map_err(|_| allocation_failure("reserve column definitions"))?;
    for raw in raw_columns {
        let name_bytes = take_name(bytes, offset)?;
        let duplicate = contains_name(
            columns
                .iter()
                .map(|column: &ColumnDefinition| column.name.raw_bytes()),
            columns.len(),
            name_bytes,
            budget,
        )
        .map_err(TableDefinitionError::Resource)?;
        if name_bytes.is_empty() || duplicate {
            return Err(TableDefinitionError::InvalidColumnName {
                ordinal: raw.ordinal.get(),
                duplicate,
            });
        }
        let name =
            DefinitionName::from_raw(name_bytes, budget).map_err(TableDefinitionError::Resource)?;
        columns.push(ColumnDefinition {
            name,
            ordinal: raw.ordinal,
            physical_type: raw.physical_type,
            storage: raw.storage,
            size: raw.size,
            auto_increment: raw.auto_increment,
            raw_variable_counter: raw.raw_variable_counter,
            sourced_constant: raw.sourced_constant,
            raw_encoding_context: raw.raw_encoding_context,
            raw_class_flags: raw.raw_class_flags,
            raw_record: raw.raw_record,
        });
    }
    Ok(columns)
}

#[derive(Debug)]
struct RawColumn {
    ordinal: ColumnOrdinal,
    physical_type: ColumnPhysicalType,
    storage: ColumnStorageClass,
    size: u16,
    auto_increment: bool,
    raw_variable_counter: u16,
    sourced_constant: u16,
    raw_encoding_context: [u8; 4],
    raw_class_flags: u8,
    raw_record: [u8; COLUMN_RECORD_LEN],
}

fn decode_type(ordinal: u16, raw: u8) -> Result<ColumnPhysicalType, TableDefinitionError> {
    match raw {
        1 => Ok(ColumnPhysicalType::Boolean),
        2 => Ok(ColumnPhysicalType::Byte),
        3 => Ok(ColumnPhysicalType::Integer),
        4 => Ok(ColumnPhysicalType::Long),
        5 => Ok(ColumnPhysicalType::Currency),
        6 => Ok(ColumnPhysicalType::Single),
        7 => Ok(ColumnPhysicalType::Double),
        8 => Ok(ColumnPhysicalType::DateTime),
        9 => Ok(ColumnPhysicalType::Binary),
        10 => Ok(ColumnPhysicalType::Text),
        11 => Ok(ColumnPhysicalType::LongBinary),
        12 => Ok(ColumnPhysicalType::Memo),
        15 => Ok(ColumnPhysicalType::Guid),
        raw => Err(TableDefinitionError::UnsupportedPhysicalType { ordinal, raw }),
    }
}

fn validate_size(
    ordinal: u16,
    physical_type: ColumnPhysicalType,
    size: u16,
) -> Result<(), TableDefinitionError> {
    let valid = match physical_type {
        ColumnPhysicalType::Boolean | ColumnPhysicalType::Byte => size == 1,
        ColumnPhysicalType::Integer => size == 2,
        ColumnPhysicalType::Long | ColumnPhysicalType::Single => size == 4,
        ColumnPhysicalType::Currency
        | ColumnPhysicalType::Double
        | ColumnPhysicalType::DateTime => size == 8,
        ColumnPhysicalType::Guid => size == 16,
        ColumnPhysicalType::Binary | ColumnPhysicalType::Text => (1..=255).contains(&size),
        ColumnPhysicalType::LongBinary | ColumnPhysicalType::Memo => size == 0,
    };
    if valid {
        Ok(())
    } else {
        Err(TableDefinitionError::UnsupportedColumnSize {
            ordinal,
            physical_type,
            size,
        })
    }
}

fn charge_vec<T>(count: u16, budget: &mut ResourceBudget) -> Result<(), TableDefinitionError> {
    let bytes = (size_of::<T>() as u64)
        .checked_mul(u64::from(count))
        .ok_or(TableDefinitionError::Resource(Error::Arithmetic {
            operation: "size column-definition vector",
        }))?;
    budget
        .charge_allocation(ByteCount::new(bytes))
        .map_err(TableDefinitionError::Resource)
}

fn take_name<'a>(bytes: &'a [u8], offset: &mut usize) -> Result<&'a [u8], TableDefinitionError> {
    let length = usize::from(take_array::<1>(bytes, offset)?[0]);
    take(bytes, offset, length)
}

fn take_array<const N: usize>(
    bytes: &[u8],
    offset: &mut usize,
) -> Result<[u8; N], TableDefinitionError> {
    take(bytes, offset, N)?
        .try_into()
        .map_err(|_| TableDefinitionError::Truncated {
            offset: *offset,
            needed: N,
            length: bytes.len(),
        })
}

fn take<'a>(
    bytes: &'a [u8],
    offset: &mut usize,
    length: usize,
) -> Result<&'a [u8], TableDefinitionError> {
    let start = *offset;
    let end = start
        .checked_add(length)
        .ok_or(TableDefinitionError::Resource(Error::Arithmetic {
            operation: "advance column-definition offset",
        }))?;
    let value = bytes
        .get(start..end)
        .ok_or(TableDefinitionError::Truncated {
            offset: start,
            needed: length,
            length: bytes.len(),
        })?;
    *offset = end;
    Ok(value)
}

fn array_at<const N: usize>(bytes: &[u8], offset: usize) -> Result<[u8; N], TableDefinitionError> {
    let end = offset
        .checked_add(N)
        .ok_or(TableDefinitionError::Resource(Error::Arithmetic {
            operation: "locate column-definition array",
        }))?;
    bytes
        .get(offset..end)
        .and_then(|value| value.try_into().ok())
        .ok_or(TableDefinitionError::Truncated {
            offset,
            needed: N,
            length: bytes.len(),
        })
}

fn u16_at(bytes: &[u8], offset: usize) -> u16 {
    u16::from_le_bytes([bytes[offset], bytes[offset + 1]])
}

fn allocation_failure(operation: &'static str) -> TableDefinitionError {
    TableDefinitionError::Resource(Error::Io {
        operation,
        kind: std::io::ErrorKind::OutOfMemory,
    })
}
