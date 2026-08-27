//! Physical index-definition model and decoding from `EXP-0059`.

use std::mem::size_of;

use crate::column_definition::ColumnOrdinal;
use crate::index_definition::IndexDefinitionError;
use crate::{ByteCount, Error, PageGeometry, PageNumber, ResourceBudget};

pub(crate) const PHYSICAL_PREFIX_LEN: usize = 8;
pub(crate) const PHYSICAL_RECORD_LEN: usize = 39;
pub(crate) const KEY_SLOT_COUNT: usize = 10;
const KEY_SLOT_LEN: usize = 3;
const NULL_COLUMN_ORDINAL: u16 = u16::MAX;
const UNIQUE_FLAG: u8 = 0x01;
const REQUIRED_FLAG: u8 = 0x08;
pub(crate) const SUPPORTED_FLAGS: u8 = UNIQUE_FLAG | REQUIRED_FLAG;

/// Direction of one sourced index key field.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum IndexDirection {
    /// Physical direction byte 1.
    Ascending,
    /// Physical direction byte 0.
    Descending,
}

/// One column participating in a physical index key.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct IndexField {
    column: ColumnOrdinal,
    direction: IndexDirection,
}

impl IndexField {
    /// Returns the referenced table column ordinal.
    #[must_use]
    pub const fn column(self) -> ColumnOrdinal {
        self.column
    }

    /// Returns the physical key direction.
    #[must_use]
    pub const fn direction(self) -> IndexDirection {
        self.direction
    }
}

/// One row/page locator for an index data-page usage map.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct IndexUsageMapReference {
    page: PageNumber,
    row: u8,
}

impl IndexUsageMapReference {
    /// Returns the physical data page containing the usage-map row.
    #[must_use]
    pub const fn page(self) -> PageNumber {
        self.page
    }

    /// Returns the zero-based row slot.
    #[must_use]
    pub const fn row(self) -> u8 {
        self.row
    }
}

/// One immutable physical index definition.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PhysicalIndexDefinition {
    sourced_prefix: [u8; PHYSICAL_PREFIX_LEN],
    fields: Vec<IndexField>,
    usage_map: IndexUsageMapReference,
    root: PageNumber,
    unique: bool,
    required: bool,
    raw_flags: u8,
    raw_record: [u8; PHYSICAL_RECORD_LEN],
}

impl PhysicalIndexDefinition {
    /// Returns the sourced eight-byte prefix retained losslessly.
    #[must_use]
    pub const fn sourced_prefix(&self) -> &[u8; PHYSICAL_PREFIX_LEN] {
        &self.sourced_prefix
    }

    /// Returns the ordered physical key fields.
    #[must_use]
    pub fn fields(&self) -> &[IndexField] {
        &self.fields
    }

    /// Returns the index data-page usage-map locator.
    #[must_use]
    pub const fn usage_map(&self) -> IndexUsageMapReference {
        self.usage_map
    }

    /// Returns the typed index-tree root reference without traversing it.
    #[must_use]
    pub const fn root(&self) -> PageNumber {
        self.root
    }

    /// Returns whether the sourced physical flags require unique keys.
    #[must_use]
    pub const fn unique(&self) -> bool {
        self.unique
    }

    /// Returns whether the sourced physical flags require non-null keys.
    #[must_use]
    pub const fn required(&self) -> bool {
        self.required
    }

    /// Returns the lossless physical flag byte.
    #[must_use]
    pub const fn raw_flags(&self) -> u8 {
        self.raw_flags
    }

    /// Returns the complete sourced physical record.
    #[must_use]
    pub const fn raw_record(&self) -> &[u8; PHYSICAL_RECORD_LEN] {
        &self.raw_record
    }
}

pub(crate) fn decode_physical(
    physical_index: u16,
    sourced_prefix: [u8; PHYSICAL_PREFIX_LEN],
    raw_record: [u8; PHYSICAL_RECORD_LEN],
    column_count: u16,
    geometry: PageGeometry,
    budget: &mut ResourceBudget,
) -> Result<PhysicalIndexDefinition, IndexDefinitionError> {
    let mut fields = Vec::new();
    let mut unused_seen = false;
    for slot in 0..KEY_SLOT_COUNT {
        let offset = slot * KEY_SLOT_LEN;
        let ordinal = u16_at(&raw_record, offset);
        if ordinal == NULL_COLUMN_ORDINAL {
            unused_seen = true;
            continue;
        }
        let slot_u8 = u8::try_from(slot).map_err(|_| {
            IndexDefinitionError::Resource(Error::IntegerConversion {
                value: slot as u128,
                target: "u8",
            })
        })?;
        if unused_seen {
            return Err(IndexDefinitionError::KeyAfterUnusedSlot {
                physical_index,
                slot: slot_u8,
            });
        }
        if ordinal >= column_count {
            return Err(IndexDefinitionError::InvalidColumnOrdinal {
                physical_index,
                slot: slot_u8,
                ordinal,
                column_count,
            });
        }
        if fields
            .iter()
            .any(|field: &IndexField| field.column.get() == ordinal)
        {
            return Err(IndexDefinitionError::DuplicateKeyColumn {
                physical_index,
                ordinal,
            });
        }
        let direction = match raw_record[offset + 2] {
            0 => IndexDirection::Descending,
            1 => IndexDirection::Ascending,
            raw => {
                return Err(IndexDefinitionError::UnsupportedDirection {
                    physical_index,
                    slot: slot_u8,
                    raw,
                });
            }
        };
        budget
            .charge_allocation(ByteCount::new(size_of::<IndexField>() as u64))
            .map_err(IndexDefinitionError::Resource)?;
        fields
            .try_reserve_exact(1)
            .map_err(|_| allocation_failure("reserve physical index key field"))?;
        fields.push(IndexField {
            column: ColumnOrdinal::new(ordinal),
            direction,
        });
    }
    if fields.is_empty() {
        return Err(IndexDefinitionError::EmptyPhysicalIndex { physical_index });
    }
    let map_page = PageNumber::new(u64::from(u24_at(&raw_record, 31)));
    let root = PageNumber::new(u64::from(u32_at(&raw_record, 34)));
    for (role, page) in [("usage map", map_page), ("index root", root)] {
        if page.get() == 0 {
            return Err(IndexDefinitionError::NullPhysicalReference {
                physical_index,
                role,
            });
        }
        geometry.validate_reference(page).map_err(|source| {
            IndexDefinitionError::InvalidPhysicalReference {
                physical_index,
                role,
                page,
                source,
            }
        })?;
    }
    let raw_flags = raw_record[38];
    if raw_flags & !SUPPORTED_FLAGS != 0 {
        return Err(IndexDefinitionError::UnsupportedPhysicalFlags {
            physical_index,
            raw: raw_flags,
        });
    }
    Ok(PhysicalIndexDefinition {
        sourced_prefix,
        fields,
        usage_map: IndexUsageMapReference {
            page: map_page,
            row: raw_record[30],
        },
        root,
        unique: raw_flags & UNIQUE_FLAG != 0,
        required: raw_flags & REQUIRED_FLAG != 0,
        raw_flags,
        raw_record,
    })
}

fn u16_at(bytes: &[u8], offset: usize) -> u16 {
    u16::from_le_bytes([bytes[offset], bytes[offset + 1]])
}

fn u24_at(bytes: &[u8], offset: usize) -> u32 {
    u32::from(bytes[offset])
        | (u32::from(bytes[offset + 1]) << 8)
        | (u32::from(bytes[offset + 2]) << 16)
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
