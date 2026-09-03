//! Checked encoder for the logical bytes of a Jet 3 table definition, the
//! inverse of `table_definition.rs` (`EXP-0059`, `EXP-0073`, `EXP-0077`).
//!
//! The output is the contiguous logical definition that `EXP-0059` describes:
//! root bytes `[0,2048)` plus continuation payloads. Splitting a definition
//! longer than one page across continuation pages, and filling the
//! next-page reference at `[4,8)`, is left to the page assembler.

use std::fmt;

use crate::column_definition_writer::{
    ColumnSpec, ColumnStorageKind, LogicalIndexKindSpec, LogicalIndexSpec, PhysicalIndexSpec,
    SystemColumnClassSpec, physical_flags, resolve_column, validate_physical_index,
    write_column_record, write_logical_record, write_name, write_physical_record,
};
use crate::table_definition_layout::{definition_len, validate_column_layout, validate_name};
use crate::{
    BinaryWriter, ByteCount, ColumnPhysicalType, Error, MapRowLocator, PageNumber, ResourceBudget,
    TableDefinitionKind,
};

/// `EXP-0059`: four-byte definition page prefix.
const DEFINITION_PREFIX: [u8; 4] = [0x02, 0x01, 0x56, 0x43];
/// `EXP-0059`: fixed header before the physical-index prefixes.
/// `EXP-0059`: byte 20 marker.
const USER_HEADER_MARKER: u8 = 0x4e;
/// `EXP-0073`: byte 20 of every observed system definition.
const SYSTEM_HEADER_MARKER: u8 = 0x53;
/// `EXP-0059`: the logical definition ends in `ff ff`.
const TERMINATOR: [u8; 2] = [0xff, 0xff];
/// `EXP-0060`: a row stores its column count in one byte.
const MAX_COLUMN_COUNT: usize = u8::MAX as usize;
/// `EXP-0059`: primary logical indexes map to physical flags `0x09`.
const USER_PRIMARY_FLAGS: u8 = 0x09;
/// `EXP-0073`: system primary logical indexes reference flag value `0x01`.
const SYSTEM_PRIMARY_FLAGS: u8 = 0x01;

/// Exact physical-index flag values admitted by the writer.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum PhysicalIndexFlagsSpec {
    /// No observed flag bits (`0x00`), admitted for user definitions only.
    Ordinary,
    /// Unique keys (`0x01`).
    Unique,
    /// The uninterpreted system-only value `0x02` from `EXP-0073`.
    SystemUninterpreted,
    /// Required keys (`0x08`).
    Required,
    /// Unique and required keys (`0x09`), admitted for user definitions only.
    UniqueRequired,
}

/// One typed 10-byte Memo/LongBinary map group (`EXP-0077`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LongValueMapSpec {
    /// Zero-based Memo or LongBinary column ordinal.
    pub column: u16,
    /// Row holding the column's owned-page map.
    pub owned: MapRowLocator,
    /// Row holding the column's available-page map.
    pub available: MapRowLocator,
}

/// Complete description of one table definition to encode.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TableDefinitionSpec<'a> {
    /// User (`0x4e`) or system (`0x53`) definition marker and record grammar.
    pub kind: TableDefinitionKind,
    /// Columns in ordinal order; at most 255 (`EXP-0060`).
    pub columns: &'a [ColumnSpec<'a>],
    /// Exact column-record classes for a system definition; empty for a user definition.
    pub system_column_classes: &'a [SystemColumnClassSpec],
    /// Physical indexes in creation order.
    pub physical_indexes: &'a [PhysicalIndexSpec<'a>],
    /// Logical indexes in stored order; every physical index must be referenced.
    pub indexes: &'a [LogicalIndexSpec<'a>],
    /// Row holding the table's owned-page map.
    pub owned_map: MapRowLocator,
    /// Row holding the table's available-page map.
    pub available_map: MapRowLocator,
    /// Current number of table rows (`EXP-0073`).
    pub row_count: u32,
    /// Typed map groups covering every Memo and LongBinary column.
    pub long_value_maps: &'a [LongValueMapSpec],
}

/// Structured failure while validating or encoding a table definition.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum TableDefinitionWriteError {
    /// More columns than a row can count.
    TooManyColumns {
        /// Requested column count.
        count: usize,
        /// Maximum encodable count.
        maximum: usize,
    },
    /// More indexes than a 16-bit count can hold.
    TooManyIndexes {
        /// `"physical"` or `"logical"`.
        role: &'static str,
        /// Requested count.
        count: usize,
    },
    /// More long-value map groups than table columns were supplied.
    TooManyLongValueMaps {
        /// Requested group count.
        count: usize,
        /// Maximum possible group count for this definition.
        maximum: usize,
    },
    /// A column or logical index name is empty.
    EmptyName {
        /// `"column"` or `"logical index"`.
        role: &'static str,
        /// Zero-based ordinal.
        ordinal: u16,
    },
    /// A name exceeds its one-byte length prefix.
    NameTooLong {
        /// `"column"` or `"logical index"`.
        role: &'static str,
        /// Zero-based ordinal.
        ordinal: u16,
        /// Requested length.
        length: usize,
        /// Maximum length.
        maximum: usize,
    },
    /// A name repeats an earlier name of the same role.
    DuplicateName {
        /// `"column"` or `"logical index"`.
        role: &'static str,
        /// Zero-based ordinal of the repeated name.
        ordinal: u16,
    },
    /// The system class inventory does not match the definition kind and columns.
    InvalidSystemColumnClassCount {
        /// Definition marker class.
        kind: TableDefinitionKind,
        /// Supplied class count.
        count: usize,
        /// Required class count.
        expected: usize,
    },
    /// A system class is incompatible with its column.
    InvalidSystemColumnClass {
        /// Zero-based column ordinal.
        ordinal: u16,
        /// Supplied class, if one existed at this ordinal.
        class: Option<SystemColumnClassSpec>,
        /// Physical type at this ordinal.
        physical_type: ColumnPhysicalType,
        /// Storage kind at this ordinal.
        storage: ColumnStorageKind,
    },
    /// Fixed offsets overflowed the 16-bit offset field.
    FixedAreaTooLarge {
        /// Column whose offset could not be represented.
        ordinal: u16,
    },
    /// Even an all-null row cannot fit in one data-page row slot.
    RowLayoutTooLarge {
        /// Minimum encoded row length for the schema.
        minimum: usize,
        /// Maximum length after the page header and one directory entry.
        maximum: usize,
    },
    /// A physical index has no key fields.
    EmptyPhysicalIndex {
        /// Zero-based physical-index ordinal.
        physical_index: u16,
    },
    /// A physical index has more key fields than slots.
    TooManyKeyFields {
        /// Zero-based physical-index ordinal.
        physical_index: u16,
        /// Requested field count.
        count: usize,
        /// Slot count.
        maximum: usize,
    },
    /// A key field names a column outside the table.
    InvalidKeyColumn {
        /// Zero-based physical-index ordinal.
        physical_index: u16,
        /// Requested column ordinal.
        ordinal: u16,
        /// Table column count.
        column_count: u16,
    },
    /// A key repeats a column.
    DuplicateKeyColumn {
        /// Zero-based physical-index ordinal.
        physical_index: u16,
        /// Repeated column ordinal.
        ordinal: u16,
    },
    /// A physical page reference is zero or too large for its field.
    InvalidPhysicalReference {
        /// Zero-based physical-index ordinal.
        physical_index: u16,
        /// Semantic role of the reference.
        role: &'static str,
        /// Rejected page.
        page: PageNumber,
    },
    /// A required table usage-map page is zero or too large for its field.
    InvalidMapReference {
        /// `"owned"` or `"available"`.
        role: &'static str,
        /// Rejected page.
        page: PageNumber,
    },
    /// A physical flag value is not admitted for this definition kind.
    InvalidPhysicalFlags {
        /// Zero-based physical-index ordinal.
        physical_index: u16,
        /// Definition marker class.
        kind: TableDefinitionKind,
        /// Rejected typed flag class.
        flags: PhysicalIndexFlagsSpec,
    },
    /// A long-value map group names an invalid or repeated column.
    InvalidLongValueMapColumn {
        /// Zero-based group index.
        group: usize,
        /// Requested column ordinal.
        column: u16,
    },
    /// A Memo or LongBinary column has no typed map group.
    MissingLongValueMap {
        /// Missing zero-based column ordinal.
        column: u16,
    },
    /// A physical index targets a column type that DAO does not index.
    UnsupportedKeyColumn {
        /// Zero-based physical-index ordinal.
        physical_index: u16,
        /// Zero-based column ordinal.
        ordinal: u16,
        /// Rejected column type.
        physical_type: ColumnPhysicalType,
    },
    /// A logical index references no physical index.
    InvalidPhysicalIndexOrdinal {
        /// Zero-based logical-index ordinal.
        logical_index: u16,
        /// Requested physical ordinal.
        ordinal: u16,
        /// Physical-index count.
        physical_count: u16,
    },
    /// More than one primary logical index was requested.
    DuplicatePrimaryIndex,
    /// A primary logical index references a physical index without the exact
    /// user (`0x09`) or system (`0x01`) primary flags.
    InvalidPrimaryFlags {
        /// Zero-based logical-index ordinal.
        logical_index: u16,
        /// Flags of the referenced physical index.
        raw: u8,
    },
    /// A relationship references page zero or a page beyond 32 bits.
    InvalidRelationshipReference {
        /// Zero-based logical-index ordinal.
        logical_index: u16,
        /// Rejected page.
        page: PageNumber,
    },
    /// A physical index is not referenced by any logical index.
    UnreferencedPhysicalIndex {
        /// Zero-based physical-index ordinal.
        physical_index: u16,
    },
    /// The logical definition exceeds the 32-bit length field.
    DefinitionTooLong {
        /// Requested length.
        length: usize,
    },
    /// The output slice cannot hold the complete definition.
    OutputTooSmall {
        /// Required length.
        needed: usize,
        /// Provided length.
        available: usize,
    },
    /// Resource policy or checked arithmetic rejected the encoding.
    Resource(Error),
}

impl fmt::Display for TableDefinitionWriteError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "table definition encoding failed: {self:?}")
    }
}

impl std::error::Error for TableDefinitionWriteError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Resource(source) => Some(source),
            _ => None,
        }
    }
}

/// Returns the exact logical length of the encoded definition.
pub fn table_definition_len(
    spec: &TableDefinitionSpec<'_>,
) -> Result<usize, TableDefinitionWriteError> {
    definition_len(
        spec.columns,
        spec.indexes.iter().map(|index| index.name),
        spec.physical_indexes.len(),
        spec.long_value_maps.len(),
    )
}

/// Encodes the logical definition into `output`, returning the encoded length.
///
/// Bytes `[4,8)` (next definition page) are written as zero for the page
/// assembler to fill; bytes with no `EXP-0059` meaning are written as zero.
pub fn encode_table_definition(
    spec: &TableDefinitionSpec<'_>,
    output: &mut [u8],
    budget: &mut ResourceBudget,
) -> Result<ByteCount, TableDefinitionWriteError> {
    let counts = validate(spec, budget)?;
    let length = table_definition_len(spec)?;
    if output.len() < length {
        return Err(TableDefinitionWriteError::OutputTooSmall {
            needed: length,
            available: output.len(),
        });
    }
    let logical_length = u32::try_from(length)
        .map_err(|_| TableDefinitionWriteError::DefinitionTooLong { length })?;
    let mut writer =
        BinaryWriter::new(output, budget).map_err(TableDefinitionWriteError::Resource)?;
    write_header(&mut writer, spec, counts, logical_length)
        .map_err(TableDefinitionWriteError::Resource)?;
    for index in spec.physical_indexes {
        writer
            .write_u32_le(0)
            .and_then(|()| writer.write_u32_le(index.entry_count))
            .map_err(TableDefinitionWriteError::Resource)?;
    }
    let mut next_fixed_offset = 0_u16;
    let mut variables_seen = 0_u16;
    for (ordinal, column) in (0_u16..).zip(spec.columns) {
        let resolved = resolve_column(
            ordinal,
            column,
            spec.kind,
            spec.system_column_classes
                .get(usize::from(ordinal))
                .copied(),
            &mut next_fixed_offset,
            &mut variables_seen,
        )?;
        write_column_record(&mut writer, ordinal, column, resolved, spec.kind)
            .map_err(TableDefinitionWriteError::Resource)?;
    }
    for column in spec.columns {
        write_name(&mut writer, column.name())?;
    }
    for index in spec.physical_indexes {
        write_physical_record(&mut writer, index).map_err(TableDefinitionWriteError::Resource)?;
    }
    for index in spec.indexes {
        write_logical_record(&mut writer, index).map_err(TableDefinitionWriteError::Resource)?;
    }
    for index in spec.indexes {
        write_name(&mut writer, index.name)?;
    }
    for map in spec.long_value_maps {
        writer
            .write_u16_le(map.column)
            .and_then(|()| write_map_locator(&mut writer, map.owned))
            .and_then(|()| write_map_locator(&mut writer, map.available))
            .map_err(TableDefinitionWriteError::Resource)?;
    }
    writer
        .write_exact(&TERMINATOR)
        .map_err(TableDefinitionWriteError::Resource)?;
    Ok(ByteCount::new(writer.position().get()))
}

#[derive(Debug, Clone, Copy)]
struct Counts {
    columns: u16,
    variables: u16,
    physical: u16,
    logical: u16,
}

fn validate(
    spec: &TableDefinitionSpec<'_>,
    budget: &mut ResourceBudget,
) -> Result<Counts, TableDefinitionWriteError> {
    if spec.columns.len() > MAX_COLUMN_COUNT {
        return Err(TableDefinitionWriteError::TooManyColumns {
            count: spec.columns.len(),
            maximum: MAX_COLUMN_COUNT,
        });
    }
    let expected_system_classes = match spec.kind {
        TableDefinitionKind::User => 0,
        TableDefinitionKind::System => spec.columns.len(),
    };
    if spec.system_column_classes.len() != expected_system_classes {
        return Err(TableDefinitionWriteError::InvalidSystemColumnClassCount {
            kind: spec.kind,
            count: spec.system_column_classes.len(),
            expected: expected_system_classes,
        });
    }
    let columns = u16::try_from(spec.columns.len()).map_err(|_| {
        TableDefinitionWriteError::TooManyColumns {
            count: spec.columns.len(),
            maximum: MAX_COLUMN_COUNT,
        }
    })?;
    let physical = u16::try_from(spec.physical_indexes.len()).map_err(|_| {
        TableDefinitionWriteError::TooManyIndexes {
            role: "physical",
            count: spec.physical_indexes.len(),
        }
    })?;
    let logical = u16::try_from(spec.indexes.len()).map_err(|_| {
        TableDefinitionWriteError::TooManyIndexes {
            role: "logical",
            count: spec.indexes.len(),
        }
    })?;
    for (role, locator) in [("owned", spec.owned_map), ("available", spec.available_map)] {
        let page = locator.page();
        if page.get() == 0 || page.get() > 0x00ff_ffff {
            return Err(TableDefinitionWriteError::InvalidMapReference { role, page });
        }
    }
    // Name uniqueness is quadratic in the (bounded) count; charge it as items.
    let name_work = u64::from(columns)
        .saturating_mul(u64::from(columns))
        .saturating_add(u64::from(logical).saturating_mul(u64::from(logical)))
        .saturating_add(u64::from(physical));
    budget
        .charge_items(name_work)
        .map_err(TableDefinitionWriteError::Resource)?;

    let variables = validate_column_layout(spec.columns, spec.kind, spec.system_column_classes)?;
    for (ordinal, index) in (0_u16..).zip(spec.physical_indexes) {
        validate_physical_index(ordinal, index, spec.columns)?;
        let admitted = match spec.kind {
            TableDefinitionKind::User => index.flags != PhysicalIndexFlagsSpec::SystemUninterpreted,
            TableDefinitionKind::System => matches!(
                index.flags,
                PhysicalIndexFlagsSpec::Unique
                    | PhysicalIndexFlagsSpec::SystemUninterpreted
                    | PhysicalIndexFlagsSpec::Required
            ),
        };
        if !admitted {
            return Err(TableDefinitionWriteError::InvalidPhysicalFlags {
                physical_index: ordinal,
                kind: spec.kind,
                flags: index.flags,
            });
        }
    }
    budget
        .charge_allocation(ByteCount::new(u64::from(physical)))
        .map_err(TableDefinitionWriteError::Resource)?;
    let mut referenced = Vec::new();
    referenced
        .try_reserve_exact(usize::from(physical))
        .map_err(|_| {
            TableDefinitionWriteError::Resource(Error::Io {
                operation: "reserve physical index references",
                kind: std::io::ErrorKind::OutOfMemory,
            })
        })?;
    referenced.resize(usize::from(physical), false);
    let mut primary_seen = false;
    for (logical_index, index) in (0_u16..).zip(spec.indexes) {
        validate_name(
            "logical index",
            logical_index,
            index.name,
            spec.indexes[..usize::from(logical_index)]
                .iter()
                .map(|earlier| earlier.name),
        )?;
        let Some(physical_spec) = spec.physical_indexes.get(usize::from(index.physical_index))
        else {
            return Err(TableDefinitionWriteError::InvalidPhysicalIndexOrdinal {
                logical_index,
                ordinal: index.physical_index,
                physical_count: physical,
            });
        };
        referenced[usize::from(index.physical_index)] = true;
        match index.kind {
            LogicalIndexKindSpec::Ordinary => {}
            LogicalIndexKindSpec::Primary => {
                if primary_seen {
                    return Err(TableDefinitionWriteError::DuplicatePrimaryIndex);
                }
                primary_seen = true;
                let raw = physical_flags(physical_spec);
                let expected = match spec.kind {
                    TableDefinitionKind::User => USER_PRIMARY_FLAGS,
                    TableDefinitionKind::System => SYSTEM_PRIMARY_FLAGS,
                };
                if raw != expected {
                    return Err(TableDefinitionWriteError::InvalidPrimaryFlags {
                        logical_index,
                        raw,
                    });
                }
            }
            LogicalIndexKindSpec::Relationship { related_table, .. } => {
                if related_table.get() == 0 || related_table.get() > u64::from(u32::MAX) {
                    return Err(TableDefinitionWriteError::InvalidRelationshipReference {
                        logical_index,
                        page: related_table,
                    });
                }
            }
        }
    }
    if let Some(physical_index) = (0..physical).find(|ordinal| !referenced[usize::from(*ordinal)]) {
        return Err(TableDefinitionWriteError::UnreferencedPhysicalIndex { physical_index });
    }
    if spec.long_value_maps.len() > spec.columns.len() {
        return Err(TableDefinitionWriteError::TooManyLongValueMaps {
            count: spec.long_value_maps.len(),
            maximum: spec.columns.len(),
        });
    }
    let long_value_work = (spec.long_value_maps.len() as u64)
        .saturating_mul(spec.long_value_maps.len() as u64)
        .saturating_add(u64::from(columns));
    budget
        .charge_items(long_value_work)
        .map_err(TableDefinitionWriteError::Resource)?;
    validate_long_value_maps(spec)?;
    Ok(Counts {
        columns,
        variables,
        physical,
        logical,
    })
}

fn validate_long_value_maps(
    spec: &TableDefinitionSpec<'_>,
) -> Result<(), TableDefinitionWriteError> {
    for (group, map) in spec.long_value_maps.iter().enumerate() {
        let valid_column = spec
            .columns
            .get(usize::from(map.column))
            .is_some_and(|column| {
                matches!(
                    column.physical_type(),
                    ColumnPhysicalType::Memo | ColumnPhysicalType::LongBinary
                )
            });
        let duplicate = spec.long_value_maps[..group]
            .iter()
            .any(|earlier| earlier.column == map.column);
        if !valid_column || duplicate {
            return Err(TableDefinitionWriteError::InvalidLongValueMapColumn {
                group,
                column: map.column,
            });
        }
        for (role, locator) in [
            ("long-value owned", map.owned),
            ("long-value available", map.available),
        ] {
            let page = locator.page();
            if page.get() == 0 || page.get() > 0x00ff_ffff {
                return Err(TableDefinitionWriteError::InvalidMapReference { role, page });
            }
        }
    }
    if let Some((column, _)) = spec.columns.iter().enumerate().find(|(ordinal, column)| {
        matches!(
            column.physical_type(),
            ColumnPhysicalType::Memo | ColumnPhysicalType::LongBinary
        ) && !spec
            .long_value_maps
            .iter()
            .any(|map| usize::from(map.column) == *ordinal)
    }) {
        return Err(TableDefinitionWriteError::MissingLongValueMap {
            column: column as u16,
        });
    }
    Ok(())
}

fn write_header(
    writer: &mut BinaryWriter<'_, '_>,
    spec: &TableDefinitionSpec<'_>,
    counts: Counts,
    logical_length: u32,
) -> Result<(), Error> {
    writer.write_exact(&DEFINITION_PREFIX)?;
    writer.write_u32_le(0)?;
    writer.write_u32_le(logical_length)?;
    writer.write_u32_le(spec.row_count)?;
    writer.write_exact(&[0; 4])?;
    writer.write_u8(match spec.kind {
        TableDefinitionKind::User => USER_HEADER_MARKER,
        TableDefinitionKind::System => SYSTEM_HEADER_MARKER,
    })?;
    writer.write_u16_le(counts.columns)?;
    writer.write_u16_le(counts.variables)?;
    writer.write_u16_le(counts.columns)?;
    writer.write_u16_le(counts.logical)?;
    writer.write_u16_le(0)?;
    writer.write_u16_le(counts.physical)?;
    writer.write_exact(&[0; 2])?;
    write_map_locator(writer, spec.owned_map)?;
    write_map_locator(writer, spec.available_map)
}

/// `EXP-0059` via `EXP-0057`: one row byte then a three-byte little-endian page.
fn write_map_locator(
    writer: &mut BinaryWriter<'_, '_>,
    locator: MapRowLocator,
) -> Result<(), Error> {
    let page = u32::try_from(locator.page().get())
        .ok()
        .filter(|page| *page <= 0x00ff_ffff)
        .ok_or(Error::IntegerConversion {
            value: u128::from(locator.page().get()),
            target: "u24",
        })?;
    writer.write_u8(locator.row())?;
    writer.write_exact(&page.to_le_bytes()[..3])
}

#[cfg(test)]
#[path = "table_definition_writer_tests.rs"]
mod tests;
