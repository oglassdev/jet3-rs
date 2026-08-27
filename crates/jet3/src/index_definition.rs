//! Lossless physical and logical index definitions from `EXP-0059`.
//!
//! This module decodes references and metadata only. It never traverses an
//! index root.

use std::fmt;
use std::mem::size_of;

use crate::definition_name::{DefinitionName, contains_name};
pub use crate::physical_index_definition::{
    IndexDirection, IndexField, IndexUsageMapReference, PhysicalIndexDefinition,
};
use crate::physical_index_definition::{
    KEY_SLOT_COUNT, PHYSICAL_PREFIX_LEN, PHYSICAL_RECORD_LEN, SUPPORTED_FLAGS, decode_physical,
};
use crate::{ByteCount, Error, PageGeometry, PageNumber, ResourceBudget};

const LOGICAL_RECORD_LEN: usize = 20;

/// Which table side supplied an observed relationship index record.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum RelationshipSide {
    /// Byte 8 value 1, observed on the DAO relation's primary table.
    PrimaryTable,
    /// Byte 8 value 2, observed on the DAO relation's foreign table.
    ForeignTable,
}

/// Lossless minimum relationship metadata sourced from one logical index.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct RelationshipReference {
    side: RelationshipSide,
    related_table: PageNumber,
    raw_selector: u32,
    raw_relation_ordinal: u32,
}

impl RelationshipReference {
    /// Returns the table side correlated by the controlled DAO snapshot.
    #[must_use]
    pub const fn side(self) -> RelationshipSide {
        self.side
    }

    /// Returns the related table-definition root.
    #[must_use]
    pub const fn related_table(self) -> PageNumber {
        self.related_table
    }

    /// Returns the first sourced selector without assigning further meaning.
    #[must_use]
    pub const fn raw_selector(self) -> u32 {
        self.raw_selector
    }

    /// Returns the sourced relationship ordinal without cascade interpretation.
    #[must_use]
    pub const fn raw_relation_ordinal(self) -> u32 {
        self.raw_relation_ordinal
    }
}

/// Interpreted class of one logical index definition.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum IndexDefinitionKind {
    /// Ordinary logical index, physical class byte 0.
    Ordinary,
    /// Primary logical index, physical class byte 1.
    Primary,
    /// Minimum relationship record, physical class byte 2.
    Relationship(RelationshipReference),
}

/// One immutable named logical index definition.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IndexDefinition {
    name: DefinitionName,
    physical_index: u16,
    kind: IndexDefinitionKind,
    raw_record: [u8; LOGICAL_RECORD_LEN],
}

impl IndexDefinition {
    /// Returns the lossless logical index name.
    #[must_use]
    pub const fn name(&self) -> &DefinitionName {
        &self.name
    }

    /// Returns the referenced physical-index ordinal.
    #[must_use]
    pub const fn physical_index(&self) -> u16 {
        self.physical_index
    }

    /// Returns the interpreted logical class.
    #[must_use]
    pub const fn kind(&self) -> IndexDefinitionKind {
        self.kind
    }

    /// Returns the complete sourced logical record.
    #[must_use]
    pub const fn raw_record(&self) -> &[u8; LOGICAL_RECORD_LEN] {
        &self.raw_record
    }
}

/// Structured corruption in physical or logical index definitions.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum IndexDefinitionError {
    /// The definition ended before a complete sourced record or name.
    Truncated {
        offset: usize,
        needed: usize,
        length: usize,
    },
    /// A used key slot followed an unused `0xffff` slot.
    KeyAfterUnusedSlot { physical_index: u16, slot: u8 },
    /// A used key slot references an invalid table column.
    InvalidColumnOrdinal {
        physical_index: u16,
        slot: u8,
        ordinal: u16,
        column_count: u16,
    },
    /// One physical key repeats a table column.
    DuplicateKeyColumn { physical_index: u16, ordinal: u16 },
    /// A used key slot has an unsupported direction byte.
    UnsupportedDirection {
        physical_index: u16,
        slot: u8,
        raw: u8,
    },
    /// A physical index has no used key fields.
    EmptyPhysicalIndex { physical_index: u16 },
    /// Physical flags contain an unobserved bit.
    UnsupportedPhysicalFlags { physical_index: u16, raw: u8 },
    /// A required physical reference is zero.
    NullPhysicalReference {
        physical_index: u16,
        role: &'static str,
    },
    /// A physical reference is beyond captured geometry.
    InvalidPhysicalReference {
        physical_index: u16,
        role: &'static str,
        page: PageNumber,
        source: Error,
    },
    /// A logical definition references no physical index.
    InvalidPhysicalIndexOrdinal {
        logical_index: u16,
        ordinal: u32,
        physical_count: u16,
    },
    /// An ordinary index's two physical selectors differ.
    InconsistentOrdinarySelectors {
        logical_index: u16,
        first: u32,
        second: u32,
    },
    /// A logical record has unsupported class/context fields.
    UnsupportedLogicalRecord {
        logical_index: u16,
        class: u8,
        context: [u8; 2],
        marker: u8,
    },
    /// An ordinary logical record carries relationship-only fields.
    UnexpectedOrdinaryRelationshipFields { logical_index: u16 },
    /// A relationship reference is zero, self-referential, or out of range.
    InvalidRelationshipReference {
        logical_index: u16,
        page: PageNumber,
        source: Option<Error>,
    },
    /// More than one primary logical index was declared.
    DuplicatePrimaryIndex,
    /// A primary logical record does not map to unique+required physical flags.
    InvalidPrimaryFlags { logical_index: u16, raw: u8 },
    /// A logical index name is empty or duplicates an earlier name.
    InvalidLogicalName { logical_index: u16, duplicate: bool },
    /// At least one physical record is unreachable from the logical records.
    UnreferencedPhysicalIndex { physical_index: u16 },
    /// Resource policy rejected count work or owned storage.
    Resource(Error),
}

impl fmt::Display for IndexDefinitionError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "invalid table index definition: {self:?}")
    }
}

impl std::error::Error for IndexDefinitionError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::InvalidPhysicalReference { source, .. } | Self::Resource(source) => Some(source),
            Self::InvalidRelationshipReference {
                source: Some(source),
                ..
            } => Some(source),
            _ => None,
        }
    }
}

#[derive(Debug)]
pub(crate) struct DecodedIndexes {
    pub(crate) physical: Vec<PhysicalIndexDefinition>,
    pub(crate) logical: Vec<IndexDefinition>,
}

pub(crate) struct IndexDecodeContext<'a> {
    pub(crate) bytes: &'a [u8],
    pub(crate) offset: &'a mut usize,
    pub(crate) prefix_offset: usize,
    pub(crate) column_count: u16,
    pub(crate) logical_count: u16,
    pub(crate) physical_count: u16,
    pub(crate) table_root: PageNumber,
    pub(crate) geometry: PageGeometry,
}

pub(crate) fn decode_indexes(
    context: IndexDecodeContext<'_>,
    budget: &mut ResourceBudget,
) -> Result<DecodedIndexes, IndexDefinitionError> {
    let item_count = u64::from(context.physical_count)
        .checked_mul(KEY_SLOT_COUNT as u64)
        .and_then(|value| value.checked_add(u64::from(context.physical_count)))
        .and_then(|value| value.checked_add(u64::from(context.logical_count)))
        .ok_or(IndexDefinitionError::Resource(Error::Arithmetic {
            operation: "count index-definition work",
        }))?;
    budget
        .charge_items(item_count)
        .map_err(IndexDefinitionError::Resource)?;
    charge_vec::<PhysicalIndexDefinition>(context.physical_count, budget)?;
    charge_vec::<IndexDefinition>(context.logical_count, budget)?;

    let mut physical = Vec::new();
    physical
        .try_reserve_exact(usize::from(context.physical_count))
        .map_err(|_| allocation_failure("reserve physical index definitions"))?;
    for ordinal in 0..context.physical_count {
        let prefix_start = context
            .prefix_offset
            .checked_add(usize::from(ordinal) * PHYSICAL_PREFIX_LEN)
            .ok_or(IndexDefinitionError::Resource(Error::Arithmetic {
                operation: "locate physical index prefix",
            }))?;
        let sourced_prefix = array_at::<PHYSICAL_PREFIX_LEN>(context.bytes, prefix_start)?;
        let raw_record = take_array::<PHYSICAL_RECORD_LEN>(context.bytes, context.offset)?;
        physical.push(decode_physical(
            ordinal,
            sourced_prefix,
            raw_record,
            context.column_count,
            context.geometry,
            budget,
        )?);
    }

    let mut raw_logical = Vec::new();
    charge_vec::<[u8; LOGICAL_RECORD_LEN]>(context.logical_count, budget)?;
    raw_logical
        .try_reserve_exact(usize::from(context.logical_count))
        .map_err(|_| allocation_failure("reserve raw logical index records"))?;
    for _ in 0..context.logical_count {
        raw_logical.push(take_array::<LOGICAL_RECORD_LEN>(
            context.bytes,
            context.offset,
        )?);
    }

    let mut names = Vec::new();
    charge_vec::<DefinitionName>(context.logical_count, budget)?;
    names
        .try_reserve_exact(usize::from(context.logical_count))
        .map_err(|_| allocation_failure("reserve logical index names"))?;
    for logical_index in 0..context.logical_count {
        let raw = take_name(context.bytes, context.offset)?;
        if raw.is_empty() {
            return Err(IndexDefinitionError::InvalidLogicalName {
                logical_index,
                duplicate: false,
            });
        }
        let duplicate = contains_name(
            names.iter().map(DefinitionName::raw_bytes),
            names.len(),
            raw,
            budget,
        )
        .map_err(IndexDefinitionError::Resource)?;
        if duplicate {
            return Err(IndexDefinitionError::InvalidLogicalName {
                logical_index,
                duplicate: true,
            });
        }
        names.push(DefinitionName::from_raw(raw, budget).map_err(IndexDefinitionError::Resource)?);
    }

    let mut logical = Vec::new();
    logical
        .try_reserve_exact(usize::from(context.logical_count))
        .map_err(|_| allocation_failure("reserve logical index definitions"))?;
    let referenced_len = usize::from(context.physical_count);
    budget
        .charge_allocation(
            ByteCount::from_usize(referenced_len).map_err(IndexDefinitionError::Resource)?,
        )
        .map_err(IndexDefinitionError::Resource)?;
    let mut referenced = Vec::new();
    referenced
        .try_reserve_exact(referenced_len)
        .map_err(|_| allocation_failure("reserve physical index references"))?;
    referenced.resize(referenced_len, false);
    let mut primary_seen = false;
    for (logical_index, (raw_record, name)) in raw_logical.into_iter().zip(names).enumerate() {
        let logical_index = u16::try_from(logical_index).map_err(|_| {
            IndexDefinitionError::Resource(Error::IntegerConversion {
                value: logical_index as u128,
                target: "u16",
            })
        })?;
        let first = u32_at(&raw_record, 0);
        let second = u32_at(&raw_record, 4);
        let physical_ordinal =
            validate_physical_ordinal(logical_index, second, context.physical_count)?;
        referenced[usize::from(physical_ordinal)] = true;
        let marker = raw_record[8];
        let relation_ordinal = u32_at(&raw_record, 9);
        let related_raw = u32_at(&raw_record, 13);
        let context_bytes = [raw_record[17], raw_record[18]];
        let class = raw_record[19];
        let kind = match class {
            0 | 1 => {
                if marker != 0 || context_bytes != [4, 4] {
                    return Err(IndexDefinitionError::UnsupportedLogicalRecord {
                        logical_index,
                        class,
                        context: context_bytes,
                        marker,
                    });
                }
                if first != second {
                    return Err(IndexDefinitionError::InconsistentOrdinarySelectors {
                        logical_index,
                        first,
                        second,
                    });
                }
                if relation_ordinal != u32::MAX || related_raw != 0 {
                    return Err(IndexDefinitionError::UnexpectedOrdinaryRelationshipFields {
                        logical_index,
                    });
                }
                if class == 1 {
                    if primary_seen {
                        return Err(IndexDefinitionError::DuplicatePrimaryIndex);
                    }
                    primary_seen = true;
                    let flags = physical[usize::from(physical_ordinal)].raw_flags();
                    if flags != SUPPORTED_FLAGS {
                        return Err(IndexDefinitionError::InvalidPrimaryFlags {
                            logical_index,
                            raw: flags,
                        });
                    }
                    IndexDefinitionKind::Primary
                } else {
                    IndexDefinitionKind::Ordinary
                }
            }
            2 => {
                if context_bytes != [1, 1] {
                    return Err(IndexDefinitionError::UnsupportedLogicalRecord {
                        logical_index,
                        class,
                        context: context_bytes,
                        marker,
                    });
                }
                let side = match marker {
                    1 => RelationshipSide::PrimaryTable,
                    2 => RelationshipSide::ForeignTable,
                    _ => {
                        return Err(IndexDefinitionError::UnsupportedLogicalRecord {
                            logical_index,
                            class,
                            context: context_bytes,
                            marker,
                        });
                    }
                };
                let related_table = PageNumber::new(u64::from(related_raw));
                if related_raw == 0 || related_table == context.table_root {
                    return Err(IndexDefinitionError::InvalidRelationshipReference {
                        logical_index,
                        page: related_table,
                        source: None,
                    });
                }
                context
                    .geometry
                    .validate_reference(related_table)
                    .map_err(
                        |source| IndexDefinitionError::InvalidRelationshipReference {
                            logical_index,
                            page: related_table,
                            source: Some(source),
                        },
                    )?;
                IndexDefinitionKind::Relationship(RelationshipReference {
                    side,
                    related_table,
                    raw_selector: first,
                    raw_relation_ordinal: relation_ordinal,
                })
            }
            _ => {
                return Err(IndexDefinitionError::UnsupportedLogicalRecord {
                    logical_index,
                    class,
                    context: context_bytes,
                    marker,
                });
            }
        };
        logical.push(IndexDefinition {
            name,
            physical_index: physical_ordinal,
            kind,
            raw_record,
        });
    }
    if let Some((ordinal, _)) = referenced.iter().enumerate().find(|(_, value)| !**value) {
        return Err(IndexDefinitionError::UnreferencedPhysicalIndex {
            physical_index: u16::try_from(ordinal).map_err(|_| {
                IndexDefinitionError::Resource(Error::IntegerConversion {
                    value: ordinal as u128,
                    target: "u16",
                })
            })?,
        });
    }
    Ok(DecodedIndexes { physical, logical })
}

fn validate_physical_ordinal(
    logical_index: u16,
    ordinal: u32,
    physical_count: u16,
) -> Result<u16, IndexDefinitionError> {
    let converted =
        u16::try_from(ordinal).map_err(|_| IndexDefinitionError::InvalidPhysicalIndexOrdinal {
            logical_index,
            ordinal,
            physical_count,
        })?;
    if converted >= physical_count {
        return Err(IndexDefinitionError::InvalidPhysicalIndexOrdinal {
            logical_index,
            ordinal,
            physical_count,
        });
    }
    Ok(converted)
}

fn charge_vec<T>(count: u16, budget: &mut ResourceBudget) -> Result<(), IndexDefinitionError> {
    let bytes = (size_of::<T>() as u64)
        .checked_mul(u64::from(count))
        .ok_or(IndexDefinitionError::Resource(Error::Arithmetic {
            operation: "size index-definition vector",
        }))?;
    budget
        .charge_allocation(ByteCount::new(bytes))
        .map_err(IndexDefinitionError::Resource)
}

fn take_name<'a>(bytes: &'a [u8], offset: &mut usize) -> Result<&'a [u8], IndexDefinitionError> {
    let length = usize::from(take_array::<1>(bytes, offset)?[0]);
    take(bytes, offset, length)
}

fn take_array<const N: usize>(
    bytes: &[u8],
    offset: &mut usize,
) -> Result<[u8; N], IndexDefinitionError> {
    let value = take(bytes, offset, N)?;
    value
        .try_into()
        .map_err(|_| IndexDefinitionError::Truncated {
            offset: *offset,
            needed: N,
            length: bytes.len(),
        })
}

fn array_at<const N: usize>(bytes: &[u8], offset: usize) -> Result<[u8; N], IndexDefinitionError> {
    let end = offset
        .checked_add(N)
        .ok_or(IndexDefinitionError::Resource(Error::Arithmetic {
            operation: "locate index-definition array",
        }))?;
    bytes
        .get(offset..end)
        .and_then(|value| value.try_into().ok())
        .ok_or(IndexDefinitionError::Truncated {
            offset,
            needed: N,
            length: bytes.len(),
        })
}

fn take<'a>(
    bytes: &'a [u8],
    offset: &mut usize,
    length: usize,
) -> Result<&'a [u8], IndexDefinitionError> {
    let start = *offset;
    let end = start
        .checked_add(length)
        .ok_or(IndexDefinitionError::Resource(Error::Arithmetic {
            operation: "advance index-definition offset",
        }))?;
    let value = bytes
        .get(start..end)
        .ok_or(IndexDefinitionError::Truncated {
            offset: start,
            needed: length,
            length: bytes.len(),
        })?;
    *offset = end;
    Ok(value)
}

fn u32_at(bytes: &[u8], offset: usize) -> u32 {
    u32::from_le_bytes([
        bytes[offset],
        bytes[offset + 1],
        bytes[offset + 2],
        bytes[offset + 3],
    ])
}

fn allocation_failure(operation: &'static str) -> IndexDefinitionError {
    IndexDefinitionError::Resource(Error::Io {
        operation,
        kind: std::io::ErrorKind::OutOfMemory,
    })
}
