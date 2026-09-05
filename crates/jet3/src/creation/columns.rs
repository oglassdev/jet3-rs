//! Typed column requests, independent of binary definition encoding.

use crate::ColumnPhysicalType;
use std::num::NonZeroU8;

/// Whether a column stores at a fixed row offset or through the variable table.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ColumnStorageKind {
    /// Fixed-offset storage; the offset is derived from preceding columns.
    Fixed,
    /// Variable storage; the index is derived from preceding columns.
    Variable,
}

/// The type of one column to create.
///
/// Each variant fixes the physical type, storage class, and size the
/// `EXP-0059` user-table records carry, so only combinations DAO accepts can
/// be described. Sizes appear only where DAO lets the caller choose one.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ColumnType {
    /// Yes/No value stored in the row presence bitmap.
    Boolean,
    /// Unsigned eight-bit integer.
    Byte,
    /// Signed 16-bit integer.
    Integer,
    /// Signed 32-bit integer.
    Long,
    /// Signed 32-bit integer the engine numbers (`EXP-0059` class 7).
    AutoIncrement,
    /// Fixed-scale currency value.
    Currency,
    /// IEEE-754 single-precision value.
    Single,
    /// IEEE-754 double-precision value.
    Double,
    /// OLE Automation date/time value.
    DateTime,
    /// Replication identifier.
    Guid,
    /// Database-code-page text of at most `max_len` bytes, stored through the
    /// variable table.
    Text {
        /// Longest value the column holds.
        max_len: NonZeroU8,
    },
    /// Database-code-page text of exactly `len` bytes at a fixed row offset.
    FixedText {
        /// Fixed value length.
        len: NonZeroU8,
    },
    /// Short binary value of at most `max_len` bytes.
    Binary {
        /// Longest value the column holds.
        max_len: NonZeroU8,
    },
    /// Memo text stored as a long value.
    Memo,
    /// OLE object stored as a long value.
    LongBinary,
}

impl ColumnType {
    /// Returns the physical type the column record carries.
    #[must_use]
    pub const fn physical_type(self) -> ColumnPhysicalType {
        match self {
            Self::Boolean => ColumnPhysicalType::Boolean,
            Self::Byte => ColumnPhysicalType::Byte,
            Self::Integer => ColumnPhysicalType::Integer,
            Self::Long | Self::AutoIncrement => ColumnPhysicalType::Long,
            Self::Currency => ColumnPhysicalType::Currency,
            Self::Single => ColumnPhysicalType::Single,
            Self::Double => ColumnPhysicalType::Double,
            Self::DateTime => ColumnPhysicalType::DateTime,
            Self::Guid => ColumnPhysicalType::Guid,
            Self::Text { .. } | Self::FixedText { .. } => ColumnPhysicalType::Text,
            Self::Binary { .. } => ColumnPhysicalType::Binary,
            Self::Memo => ColumnPhysicalType::Memo,
            Self::LongBinary => ColumnPhysicalType::LongBinary,
        }
    }

    /// Returns whether the column stores at a fixed offset or variably.
    #[must_use]
    pub const fn storage(self) -> ColumnStorageKind {
        match self {
            Self::Text { .. } | Self::Binary { .. } | Self::Memo | Self::LongBinary => {
                ColumnStorageKind::Variable
            }
            _ => ColumnStorageKind::Fixed,
        }
    }

    /// Returns the size the column record carries (`EXP-0059`): the fixed
    /// width, the declared maximum length, or zero for long values.
    #[must_use]
    pub const fn size(self) -> u16 {
        match self {
            Self::Boolean | Self::Byte => 1,
            Self::Integer => 2,
            Self::Long | Self::AutoIncrement | Self::Single => 4,
            Self::Currency | Self::Double | Self::DateTime => 8,
            Self::Guid => 16,
            Self::Text { max_len } | Self::Binary { max_len } => max_len.get() as u16,
            Self::FixedText { len } => len.get() as u16,
            Self::Memo | Self::LongBinary => 0,
        }
    }

    /// Returns whether the column is a long value with an external page map.
    #[must_use]
    pub const fn is_long_value(self) -> bool {
        matches!(self, Self::Memo | Self::LongBinary)
    }
}

/// One column to encode.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ColumnSpec<'a> {
    name: &'a [u8],
    column_type: ColumnType,
    allow_zero_length: bool,
}

impl<'a> ColumnSpec<'a> {
    /// Describes a column with raw database-code-page name bytes.
    #[must_use]
    pub const fn new(name: &'a [u8], column_type: ColumnType) -> Self {
        Self {
            name,
            column_type,
            allow_zero_length: false,
        }
    }

    /// Opts this Memo column into distinct empty-string values.
    ///
    /// Candidate construction from EXP-0200/0208, awaiting DAO validation.
    /// Supported only on the first unindexed table with Long `Id` followed by
    /// one Memo column with an ASCII alphanumeric name. Other combinations
    /// fail composition. Empty OLE remains unsupported.
    #[must_use]
    pub const fn with_allow_zero_length(mut self) -> Self {
        self.allow_zero_length = true;
        self
    }

    /// Whether distinct empty Memo strings were requested.
    #[must_use]
    pub const fn allow_zero_length(&self) -> bool {
        self.allow_zero_length
    }

    /// Returns the raw name bytes.
    #[must_use]
    pub const fn name(&self) -> &'a [u8] {
        self.name
    }

    /// Returns the column type.
    #[must_use]
    pub const fn column_type(&self) -> ColumnType {
        self.column_type
    }

    /// Returns the physical type the column record carries.
    #[must_use]
    pub const fn physical_type(&self) -> ColumnPhysicalType {
        self.column_type.physical_type()
    }

    /// Returns the storage kind the column record carries.
    #[must_use]
    pub const fn storage(&self) -> ColumnStorageKind {
        self.column_type.storage()
    }

    /// Returns the size the column record carries.
    #[must_use]
    pub const fn size(&self) -> u16 {
        self.column_type.size()
    }
}
