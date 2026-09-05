//! Checked encoders for the fixed-size column and index records inside a Jet 3
//! table definition: the inverse of `column_definition.rs`,
//! `physical_index_definition.rs`, and `index_definition.rs`.
//!
//! User record layouts come from `EXP-0059`, system distinctions from
//! `EXP-0073`, and relationship cascade bytes from `EXP-0062`. Names are raw
//! database-code-page bytes (`EXP-0059`).

use std::num::NonZeroU8;

use crate::table_definition_writer::{PhysicalIndexFlagsSpec, TableDefinitionWriteError};
use crate::{
    BinaryWriter, ColumnPhysicalType, ColumnStorageClass, Error, IndexDirection, PageNumber,
    RelationshipSide, TableDefinitionKind,
};

/// `EXP-0059`: one 18-byte physical record per column.
pub const COLUMN_RECORD_LEN: usize = 18;
/// `EXP-0059`: one 39-byte record per physical index.
pub const PHYSICAL_RECORD_LEN: usize = 39;
/// `EXP-0059`: eight sourced prefix bytes per physical index, zero in controls.
pub const PHYSICAL_PREFIX_LEN: usize = 8;
/// `EXP-0059`: one 20-byte record per logical index.
pub const LOGICAL_RECORD_LEN: usize = 20;
/// `EXP-0059`: ten three-byte key slots per physical index.
pub const KEY_SLOT_COUNT: usize = 10;
/// `EXP-0059`: names carry a one-byte length prefix.
pub const MAX_NAME_LEN: usize = u8::MAX as usize;

/// `EXP-0059`: class 2 variable, class 3 fixed, class 7 auto-increment Long.
const VARIABLE_CLASS: u8 = 2;
const FIXED_CLASS: u8 = 3;
const AUTO_INCREMENT_CLASS: u8 = 7;
/// `EXP-0073`: system variable/fixed classes and the distinct Binary class.
const SYSTEM_VARIABLE_CLASS: u8 = 0x12;
const SYSTEM_FIXED_CLASS: u8 = 0x13;
const SYSTEM_BINARY_CLASS: u8 = 0x32;
/// `EXP-0059`: sourced value 1 at column record bytes `[7,9)`.
const USER_SOURCED_CONSTANT: u16 = 1;
/// `EXP-0073`: system column records store zero in the constant field.
const SYSTEM_SOURCED_CONSTANT: u16 = 0;
/// `EXP-0059`: raw locale context bytes at column record bytes `[9,13)`.
const ENCODING_CONTEXT: [u8; 4] = [0x09, 0x04, 0xe4, 0x04];
/// `EXP-0059`: unused key slots hold ordinal `0xffff`.
const UNUSED_SLOT_ORDINAL: u16 = u16::MAX;
/// `EXP-0059`: physical flag bits `0x01` unique and `0x08` required.
/// `EXP-0059`: ordinary/primary logical records hold `0xffffffff` at `[9,13)`
/// and context `04 04` at `[17,19)`.
const ORDINARY_RELATION_ORDINAL: u32 = u32::MAX;
const ORDINARY_CONTEXT: [u8; 2] = [4, 4];
/// `EXP-0059`: logical class byte 0 ordinary, 1 primary, 2 relationship.
const ORDINARY_CLASS: u8 = 0;
const PRIMARY_CLASS: u8 = 1;
const RELATIONSHIP_CLASS: u8 = 2;
/// `EXP-0059`: three-byte little-endian usage-map page at `[31,34)`.
const MAX_U24_PAGE: u64 = 0x00ff_ffff;

/// Whether a column stores at a fixed row offset or through the variable table.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ColumnStorageKind {
    /// Fixed-offset storage; the offset is derived from preceding columns.
    Fixed,
    /// Variable storage; the index is derived from preceding columns.
    Variable,
}

/// Exact system column-record classes observed by `EXP-0073`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum SystemColumnClassSpec {
    /// Class `0x12`, including the observed ordinary Binary columns.
    Variable,
    /// Class `0x13`.
    Fixed,
    /// Class `0x32`, used only by the observed special Binary columns.
    Binary,
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
}

impl<'a> ColumnSpec<'a> {
    /// Describes a column with raw database-code-page name bytes.
    #[must_use]
    pub const fn new(name: &'a [u8], column_type: ColumnType) -> Self {
        Self { name, column_type }
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

/// One key field of a physical index.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct IndexFieldSpec {
    /// Zero-based table column ordinal.
    pub column: u16,
    /// Key direction.
    pub direction: IndexDirection,
}

/// One physical index to encode.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PhysicalIndexSpec<'a> {
    /// Ordered key fields; at most [`KEY_SLOT_COUNT`].
    pub fields: &'a [IndexFieldSpec],
    /// Data page holding the index usage-map row.
    pub usage_map_page: PageNumber,
    /// Row slot of the index usage map on that page.
    pub usage_map_row: u8,
    /// Index-tree root page.
    pub root: PageNumber,
    /// Exact admitted physical-index flag class.
    pub flags: PhysicalIndexFlagsSpec,
    /// Number of distinct keys represented by this physical index.
    pub entry_count: u32,
}

/// Interpreted class of one logical index to encode.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum LogicalIndexKindSpec {
    /// Ordinary logical index.
    Ordinary,
    /// Primary logical index using the definition-kind-specific primary flags.
    Primary,
    /// Minimum relationship record (`EXP-0059`, `EXP-0062`).
    Relationship {
        /// Which side of the relation this table plays.
        side: RelationshipSide,
        /// Related table-definition root.
        related_table: PageNumber,
        /// Sourced selector at `[0,4)`.
        raw_selector: u32,
        /// Sourced relationship ordinal at `[9,13)`.
        relation_ordinal: u32,
        /// Cascade-update flag.
        cascade_updates: bool,
        /// Cascade-delete flag.
        cascade_deletes: bool,
    },
}

/// One named logical index to encode.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LogicalIndexSpec<'a> {
    /// Raw database-code-page name bytes.
    pub name: &'a [u8],
    /// Referenced physical-index ordinal.
    pub physical_index: u16,
    /// Interpreted class.
    pub kind: LogicalIndexKindSpec,
}

/// Derived row placement of one column.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct ResolvedColumn {
    pub(crate) storage: ColumnStorageClass,
    pub(crate) variable_counter: u16,
    pub(crate) class: u8,
}

/// Derives one column's fixed offset or variable index and its record class.
pub(crate) fn resolve_column(
    ordinal: u16,
    column: &ColumnSpec<'_>,
    definition_kind: TableDefinitionKind,
    system_class: Option<SystemColumnClassSpec>,
    next_fixed_offset: &mut u16,
    variables_seen: &mut u16,
) -> Result<ResolvedColumn, TableDefinitionWriteError> {
    let physical_type = column.physical_type();
    let storage = column.storage();
    let invalid_system_class = || TableDefinitionWriteError::InvalidSystemColumnClass {
        ordinal,
        class: system_class,
        physical_type,
        storage,
    };
    let auto_increment = column.column_type() == ColumnType::AutoIncrement;
    if definition_kind == TableDefinitionKind::System && auto_increment {
        return Err(invalid_system_class());
    }
    let variable_counter = *variables_seen;
    match storage {
        ColumnStorageKind::Variable => {
            let index = *variables_seen;
            *variables_seen = index
                .checked_add(1)
                .ok_or(TableDefinitionWriteError::Resource(Error::Arithmetic {
                    operation: "advance variable column count",
                }))?;
            let class = match (definition_kind, system_class) {
                (TableDefinitionKind::User, _) => VARIABLE_CLASS,
                (TableDefinitionKind::System, Some(SystemColumnClassSpec::Variable)) => {
                    SYSTEM_VARIABLE_CLASS
                }
                (TableDefinitionKind::System, Some(SystemColumnClassSpec::Binary))
                    if physical_type == ColumnPhysicalType::Binary =>
                {
                    SYSTEM_BINARY_CLASS
                }
                (TableDefinitionKind::System, _) => return Err(invalid_system_class()),
            };
            Ok(ResolvedColumn {
                storage: ColumnStorageClass::Variable { index },
                variable_counter,
                class,
            })
        }
        ColumnStorageKind::Fixed => {
            let offset = if definition_kind == TableDefinitionKind::System
                && physical_type == ColumnPhysicalType::Boolean
            {
                // EXP-0073: MSysACEs.FInheritable carries offset zero.
                0
            } else {
                *next_fixed_offset
            };
            if physical_type != ColumnPhysicalType::Boolean {
                *next_fixed_offset = offset
                    .checked_add(column.size())
                    .ok_or(TableDefinitionWriteError::FixedAreaTooLarge { ordinal })?;
            }
            let class = match (definition_kind, system_class) {
                (TableDefinitionKind::System, Some(SystemColumnClassSpec::Fixed)) => {
                    SYSTEM_FIXED_CLASS
                }
                (TableDefinitionKind::System, _) => return Err(invalid_system_class()),
                (TableDefinitionKind::User, _) if auto_increment => AUTO_INCREMENT_CLASS,
                (TableDefinitionKind::User, _) => FIXED_CLASS,
            };
            Ok(ResolvedColumn {
                storage: ColumnStorageClass::Fixed { offset },
                variable_counter,
                class,
            })
        }
    }
}

/// Writes one 18-byte column record.
pub(crate) fn write_column_record(
    writer: &mut BinaryWriter<'_, '_>,
    ordinal: u16,
    column: &ColumnSpec<'_>,
    resolved: ResolvedColumn,
    definition_kind: TableDefinitionKind,
) -> Result<(), Error> {
    writer.write_u8(column.physical_type().raw())?;
    writer.write_u16_le(ordinal)?;
    writer.write_u16_le(resolved.variable_counter)?;
    writer.write_u16_le(match definition_kind {
        TableDefinitionKind::User => ordinal,
        TableDefinitionKind::System => 0,
    })?;
    writer.write_u16_le(match definition_kind {
        TableDefinitionKind::User => USER_SOURCED_CONSTANT,
        TableDefinitionKind::System => SYSTEM_SOURCED_CONSTANT,
    })?;
    writer.write_exact(&ENCODING_CONTEXT)?;
    writer.write_u8(resolved.class)?;
    // EXP-0059: bytes [14,16) of variable records have no assigned meaning.
    let fixed_offset = match resolved.storage {
        ColumnStorageClass::Fixed { offset } => offset,
        ColumnStorageClass::Variable { .. } => 0,
    };
    writer.write_u16_le(fixed_offset)?;
    writer.write_u16_le(column.size())
}

/// Validates a definition name and writes its length prefix and bytes.
pub(crate) fn write_name(
    writer: &mut BinaryWriter<'_, '_>,
    name: &[u8],
) -> Result<(), TableDefinitionWriteError> {
    let length = u8::try_from(name.len()).map_err(|_| {
        TableDefinitionWriteError::Resource(Error::IntegerConversion {
            value: name.len() as u128,
            target: "u8",
        })
    })?;
    writer
        .write_u8(length)
        .and_then(|()| writer.write_exact(name))
        .map_err(TableDefinitionWriteError::Resource)
}

/// Validates one physical index against the table column count.
pub(crate) fn validate_physical_index(
    physical_index: u16,
    index: &PhysicalIndexSpec<'_>,
    columns: &[ColumnSpec<'_>],
) -> Result<(), TableDefinitionWriteError> {
    let column_count =
        u16::try_from(columns.len()).map_err(|_| TableDefinitionWriteError::TooManyColumns {
            count: columns.len(),
            maximum: u8::MAX as usize,
        })?;
    if index.fields.is_empty() {
        return Err(TableDefinitionWriteError::EmptyPhysicalIndex { physical_index });
    }
    if index.fields.len() > KEY_SLOT_COUNT {
        return Err(TableDefinitionWriteError::TooManyKeyFields {
            physical_index,
            count: index.fields.len(),
            maximum: KEY_SLOT_COUNT,
        });
    }
    for (slot, field) in index.fields.iter().enumerate() {
        if field.column >= column_count {
            return Err(TableDefinitionWriteError::InvalidKeyColumn {
                physical_index,
                ordinal: field.column,
                column_count,
            });
        }
        let physical_type = columns[usize::from(field.column)].physical_type();
        if matches!(
            physical_type,
            ColumnPhysicalType::LongBinary | ColumnPhysicalType::Memo
        ) {
            return Err(TableDefinitionWriteError::UnsupportedKeyColumn {
                physical_index,
                ordinal: field.column,
                physical_type,
            });
        }
        if index.fields[..slot]
            .iter()
            .any(|earlier| earlier.column == field.column)
        {
            return Err(TableDefinitionWriteError::DuplicateKeyColumn {
                physical_index,
                ordinal: field.column,
            });
        }
    }
    for (role, page, maximum) in [
        ("usage map", index.usage_map_page, MAX_U24_PAGE),
        ("index root", index.root, u64::from(u32::MAX)),
    ] {
        if page.get() == 0 || page.get() > maximum {
            return Err(TableDefinitionWriteError::InvalidPhysicalReference {
                physical_index,
                role,
                page,
            });
        }
    }
    Ok(())
}

/// Returns the `EXP-0059` flag byte for one physical index.
pub(crate) const fn physical_flags(index: &PhysicalIndexSpec<'_>) -> u8 {
    index.flags.raw()
}

/// Writes one validated 39-byte physical-index record.
pub(crate) fn write_physical_record(
    writer: &mut BinaryWriter<'_, '_>,
    index: &PhysicalIndexSpec<'_>,
) -> Result<(), Error> {
    for slot in 0..KEY_SLOT_COUNT {
        match index.fields.get(slot) {
            Some(field) => {
                writer.write_u16_le(field.column)?;
                // EXP-0059: direction 0 descending, 1 ascending.
                writer.write_u8(match field.direction {
                    IndexDirection::Descending => 0,
                    IndexDirection::Ascending => 1,
                })?;
            }
            None => {
                writer.write_u16_le(UNUSED_SLOT_ORDINAL)?;
                // EXP-0059: direction bytes of unused slots are uninterpreted.
                writer.write_u8(0)?;
            }
        }
    }
    writer.write_u8(index.usage_map_row)?;
    let page = u32::try_from(index.usage_map_page.get()).map_err(|_| Error::IntegerConversion {
        value: u128::from(index.usage_map_page.get()),
        target: "u24",
    })?;
    writer.write_exact(&page.to_le_bytes()[..3])?;
    let root = u32::try_from(index.root.get()).map_err(|_| Error::IntegerConversion {
        value: u128::from(index.root.get()),
        target: "u32",
    })?;
    writer.write_u32_le(root)?;
    writer.write_u8(physical_flags(index))
}

/// Writes one validated 20-byte logical-index record.
pub(crate) fn write_logical_record(
    writer: &mut BinaryWriter<'_, '_>,
    index: &LogicalIndexSpec<'_>,
) -> Result<(), Error> {
    let physical = u32::from(index.physical_index);
    match index.kind {
        LogicalIndexKindSpec::Ordinary | LogicalIndexKindSpec::Primary => {
            writer.write_u32_le(physical)?;
            writer.write_u32_le(physical)?;
            writer.write_u8(0)?;
            writer.write_u32_le(ORDINARY_RELATION_ORDINAL)?;
            writer.write_u32_le(0)?;
            writer.write_exact(&ORDINARY_CONTEXT)?;
            writer.write_u8(if index.kind == LogicalIndexKindSpec::Primary {
                PRIMARY_CLASS
            } else {
                ORDINARY_CLASS
            })
        }
        LogicalIndexKindSpec::Relationship {
            side,
            related_table,
            raw_selector,
            relation_ordinal,
            cascade_updates,
            cascade_deletes,
        } => {
            writer.write_u32_le(raw_selector)?;
            writer.write_u32_le(physical)?;
            // EXP-0059: byte 8 value 1 on the primary table, 2 on the foreign.
            writer.write_u8(match side {
                RelationshipSide::PrimaryTable => 1,
                RelationshipSide::ForeignTable => 2,
            })?;
            writer.write_u32_le(relation_ordinal)?;
            let related =
                u32::try_from(related_table.get()).map_err(|_| Error::IntegerConversion {
                    value: u128::from(related_table.get()),
                    target: "u32",
                })?;
            writer.write_u32_le(related)?;
            // EXP-0062: cascade-update then cascade-delete flag bytes.
            writer.write_u8(u8::from(cascade_updates))?;
            writer.write_u8(u8::from(cascade_deletes))?;
            writer.write_u8(RELATIONSHIP_CLASS)
        }
    }
}

/// Builds a column size from a nonzero test literal.
#[cfg(test)]
pub(crate) const fn nz(value: u8) -> NonZeroU8 {
    match NonZeroU8::new(value) {
        Some(size) => size,
        None => NonZeroU8::MIN,
    }
}
