//! Checked encoders for the fixed-size column and index records inside a Jet 3
//! table definition: the inverse of `column_definition.rs`,
//! `physical_index_definition.rs`, and `index_definition.rs`.
//!
//! Record layouts come from `EXP-0059`; relationship cascade bytes from
//! `EXP-0062`. Names are raw database-code-page bytes (`EXP-0059`).

use crate::table_definition_writer::TableDefinitionWriteError;
use crate::{
    BinaryWriter, ColumnPhysicalType, ColumnStorageClass, Error, IndexDirection, PageNumber,
    RelationshipSide,
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
/// `EXP-0059`: sourced value 1 at column record bytes `[7,9)`.
const SOURCED_CONSTANT: u16 = 1;
/// `EXP-0059`: raw locale context bytes at column record bytes `[9,13)`.
const ENCODING_CONTEXT: [u8; 4] = [0x09, 0x04, 0xe4, 0x04];
/// `EXP-0059`: unused key slots hold ordinal `0xffff`.
const UNUSED_SLOT_ORDINAL: u16 = u16::MAX;
/// `EXP-0059`: physical flag bits `0x01` unique and `0x08` required.
const UNIQUE_FLAG: u8 = 0x01;
const REQUIRED_FLAG: u8 = 0x08;
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

/// One column to encode.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ColumnSpec<'a> {
    name: &'a [u8],
    physical_type: ColumnPhysicalType,
    storage: ColumnStorageKind,
    size: u16,
    auto_increment: bool,
}

impl<'a> ColumnSpec<'a> {
    /// Describes a column with raw database-code-page name bytes.
    #[must_use]
    pub const fn new(
        name: &'a [u8],
        physical_type: ColumnPhysicalType,
        storage: ColumnStorageKind,
        size: u16,
    ) -> Self {
        Self {
            name,
            physical_type,
            storage,
            size,
            auto_increment: false,
        }
    }

    /// Marks the column as the auto-increment Long (`EXP-0059` class 7).
    #[must_use]
    pub const fn with_auto_increment(mut self) -> Self {
        self.auto_increment = true;
        self
    }

    /// Returns the raw name bytes.
    #[must_use]
    pub const fn name(&self) -> &'a [u8] {
        self.name
    }

    /// Returns the physical type.
    #[must_use]
    pub const fn physical_type(&self) -> ColumnPhysicalType {
        self.physical_type
    }

    /// Returns the requested storage kind.
    #[must_use]
    pub const fn storage(&self) -> ColumnStorageKind {
        self.storage
    }

    /// Returns the declared fixed or maximum size.
    #[must_use]
    pub const fn size(&self) -> u16 {
        self.size
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
    /// Whether keys must be unique (`EXP-0059` flag `0x01`).
    pub unique: bool,
    /// Whether keys must be non-null (`EXP-0059` flag `0x08`).
    pub required: bool,
}

/// Interpreted class of one logical index to encode.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum LogicalIndexKindSpec {
    /// Ordinary logical index.
    Ordinary,
    /// Primary logical index; its physical index must be unique and required.
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

/// Validates one column and derives its fixed offset or variable index.
pub(crate) fn resolve_column(
    ordinal: u16,
    column: &ColumnSpec<'_>,
    next_fixed_offset: &mut u16,
    variables_seen: &mut u16,
) -> Result<ResolvedColumn, TableDefinitionWriteError> {
    let physical_type = column.physical_type;
    validate_size(ordinal, physical_type, column.size)?;
    let long_or_binary = matches!(
        physical_type,
        ColumnPhysicalType::Binary | ColumnPhysicalType::LongBinary | ColumnPhysicalType::Memo
    );
    let class_error = TableDefinitionWriteError::UnsupportedColumnClass {
        ordinal,
        physical_type,
        storage: column.storage,
    };
    let variable_counter = *variables_seen;
    match column.storage {
        ColumnStorageKind::Variable => {
            if !(long_or_binary || physical_type == ColumnPhysicalType::Text)
                || column.auto_increment
            {
                return Err(class_error);
            }
            let index = *variables_seen;
            *variables_seen = index
                .checked_add(1)
                .ok_or(TableDefinitionWriteError::Resource(Error::Arithmetic {
                    operation: "advance variable column count",
                }))?;
            Ok(ResolvedColumn {
                storage: ColumnStorageClass::Variable { index },
                variable_counter,
                class: VARIABLE_CLASS,
            })
        }
        ColumnStorageKind::Fixed => {
            if long_or_binary
                || (column.auto_increment && physical_type != ColumnPhysicalType::Long)
            {
                return Err(class_error);
            }
            let offset = *next_fixed_offset;
            if physical_type != ColumnPhysicalType::Boolean {
                *next_fixed_offset = offset
                    .checked_add(column.size)
                    .ok_or(TableDefinitionWriteError::FixedAreaTooLarge { ordinal })?;
            }
            Ok(ResolvedColumn {
                storage: ColumnStorageClass::Fixed { offset },
                variable_counter,
                class: if column.auto_increment {
                    AUTO_INCREMENT_CLASS
                } else {
                    FIXED_CLASS
                },
            })
        }
    }
}

fn validate_size(
    ordinal: u16,
    physical_type: ColumnPhysicalType,
    size: u16,
) -> Result<(), TableDefinitionWriteError> {
    // EXP-0059: accepted DAO sizes per physical type.
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
        Err(TableDefinitionWriteError::UnsupportedColumnSize {
            ordinal,
            physical_type,
            size,
        })
    }
}

/// Writes one 18-byte column record.
pub(crate) fn write_column_record(
    writer: &mut BinaryWriter<'_, '_>,
    ordinal: u16,
    column: &ColumnSpec<'_>,
    resolved: ResolvedColumn,
) -> Result<(), Error> {
    writer.write_u8(column.physical_type.raw())?;
    writer.write_u16_le(ordinal)?;
    writer.write_u16_le(resolved.variable_counter)?;
    writer.write_u16_le(ordinal)?;
    writer.write_u16_le(SOURCED_CONSTANT)?;
    writer.write_exact(&ENCODING_CONTEXT)?;
    writer.write_u8(resolved.class)?;
    // EXP-0059: bytes [14,16) of variable records have no assigned meaning.
    let fixed_offset = match resolved.storage {
        ColumnStorageClass::Fixed { offset } => offset,
        ColumnStorageClass::Variable { .. } => 0,
    };
    writer.write_u16_le(fixed_offset)?;
    writer.write_u16_le(column.size)
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
    let mut flags = 0;
    if index.unique {
        flags |= UNIQUE_FLAG;
    }
    if index.required {
        flags |= REQUIRED_FLAG;
    }
    flags
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
