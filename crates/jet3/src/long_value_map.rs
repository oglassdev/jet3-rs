//! Per-column long-value map locators decoded from the table-definition
//! suffix.
//!
//! `EXP-0074` fixes the group shape: one 10-byte group per Memo or LongBinary
//! column holding a little-endian column ordinal followed by owned and
//! available map locators in the `EXP-0057` row-byte-plus-24-bit-page form.
//! `EXP-0077` accepts that grammar with order-insensitive coverage and
//! correlates the owned map with newly appearing long-value pages.

use std::fmt;
use std::mem::size_of;

use crate::column_definition::{ColumnDefinition, ColumnOrdinal, ColumnPhysicalType};
use crate::{ByteCount, Error, MapRowLocator, PageGeometry, PageNumber, ResourceBudget};

/// Length of one sourced long-value map group.
pub const LONG_VALUE_MAP_GROUP_LEN: usize = 10;

/// One column's owned and available long-value map locators.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LongValueMapDefinition {
    column: ColumnOrdinal,
    owned: MapRowLocator,
    available: MapRowLocator,
    raw_group: [u8; LONG_VALUE_MAP_GROUP_LEN],
}

impl LongValueMapDefinition {
    /// Returns the Memo or LongBinary column the maps belong to.
    #[must_use]
    pub const fn column(&self) -> ColumnOrdinal {
        self.column
    }

    /// Returns the row tracking the column's owned long-value pages.
    #[must_use]
    pub const fn owned(&self) -> MapRowLocator {
        self.owned
    }

    /// Returns the second locator, named by analogy with the table maps.
    #[must_use]
    pub const fn available(&self) -> MapRowLocator {
        self.available
    }

    /// Returns the complete sourced group.
    #[must_use]
    pub const fn raw_group(&self) -> &[u8; LONG_VALUE_MAP_GROUP_LEN] {
        &self.raw_group
    }
}

/// Structured failure while decoding the long-value map suffix.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum LongValueMapError {
    /// The suffix is not a whole number of groups.
    InvalidSuffixLength {
        /// Sourced suffix length.
        length: usize,
    },
    /// A group names a column ordinal outside the definition.
    InvalidColumnOrdinal {
        /// Zero-based group index.
        group: usize,
        /// Sourced column ordinal.
        ordinal: u16,
        /// Number of columns in the definition.
        column_count: usize,
    },
    /// A group names a column that is not Memo or LongBinary.
    NotLongValueColumn {
        /// Sourced column ordinal.
        ordinal: u16,
        /// Decoded physical type of that column.
        physical_type: ColumnPhysicalType,
    },
    /// Two groups name the same column.
    DuplicateColumn {
        /// Repeated column ordinal.
        ordinal: u16,
    },
    /// A Memo or LongBinary column has no group.
    MissingColumn {
        /// Column ordinal lacking a group.
        ordinal: u16,
    },
    /// A locator names a page outside the captured input.
    InvalidReference {
        /// Column ordinal whose group holds the locator.
        ordinal: u16,
        /// Whether the owned or available locator failed.
        role: &'static str,
        /// Decoded locator.
        locator: MapRowLocator,
        /// Geometry failure, absent for a zero page.
        source: Option<Error>,
    },
    /// A locator does not name an existing data-page row.
    InvalidMapRow {
        /// Column ordinal whose group holds the locator.
        ordinal: u16,
        /// Whether the owned or available locator failed.
        role: &'static str,
        /// Decoded locator.
        locator: MapRowLocator,
        /// Row lookup failure.
        source: crate::UsageMapError,
    },
    /// Resource policy rejected decoding work or owned storage.
    Resource(Error),
}

impl fmt::Display for LongValueMapError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "long-value map suffix failed: {self:?}")
    }
}

impl std::error::Error for LongValueMapError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::InvalidReference {
                source: Some(source),
                ..
            }
            | Self::Resource(source) => Some(source),
            Self::InvalidMapRow { source, .. } => Some(source),
            _ => None,
        }
    }
}

pub(crate) fn decode_long_value_maps(
    suffix: &[u8],
    columns: &[ColumnDefinition],
    geometry: PageGeometry,
    budget: &mut ResourceBudget,
) -> Result<Vec<LongValueMapDefinition>, LongValueMapError> {
    if !suffix.len().is_multiple_of(LONG_VALUE_MAP_GROUP_LEN) {
        return Err(LongValueMapError::InvalidSuffixLength {
            length: suffix.len(),
        });
    }
    let group_count = suffix.len() / LONG_VALUE_MAP_GROUP_LEN;
    let item_work = group_count
        .checked_add(columns.len())
        .ok_or(LongValueMapError::Resource(Error::Arithmetic {
            operation: "count long-value map work",
        }))?;
    budget
        .charge_items(item_work as u64)
        .map_err(LongValueMapError::Resource)?;
    let bytes = (size_of::<LongValueMapDefinition>() as u64)
        .checked_mul(group_count as u64)
        .ok_or(LongValueMapError::Resource(Error::Arithmetic {
            operation: "size long-value map vector",
        }))?;
    budget
        .charge_allocation(ByteCount::new(bytes))
        .map_err(LongValueMapError::Resource)?;
    let mut maps = Vec::new();
    maps.try_reserve_exact(group_count).map_err(|_| {
        LongValueMapError::Resource(Error::Io {
            operation: "reserve long-value map definitions",
            kind: std::io::ErrorKind::OutOfMemory,
        })
    })?;
    budget
        .charge_allocation(ByteCount::new(columns.len() as u64))
        .map_err(LongValueMapError::Resource)?;
    let mut seen = Vec::new();
    seen.try_reserve_exact(columns.len()).map_err(|_| {
        LongValueMapError::Resource(Error::Io {
            operation: "reserve long-value map coverage",
            kind: std::io::ErrorKind::OutOfMemory,
        })
    })?;
    seen.resize(columns.len(), false);
    for (group, raw) in suffix.chunks_exact(LONG_VALUE_MAP_GROUP_LEN).enumerate() {
        let raw_group: [u8; LONG_VALUE_MAP_GROUP_LEN] = raw.try_into().map_err(|_| {
            LongValueMapError::Resource(Error::Arithmetic {
                operation: "slice long-value map group",
            })
        })?;
        let ordinal = u16::from_le_bytes([raw_group[0], raw_group[1]]);
        let column =
            columns
                .get(usize::from(ordinal))
                .ok_or(LongValueMapError::InvalidColumnOrdinal {
                    group,
                    ordinal,
                    column_count: columns.len(),
                })?;
        if !is_long_value(column) {
            return Err(LongValueMapError::NotLongValueColumn {
                ordinal,
                physical_type: column.physical_type(),
            });
        }
        if seen[usize::from(ordinal)] {
            return Err(LongValueMapError::DuplicateColumn { ordinal });
        }
        let owned = decode_locator(&raw_group[2..6]);
        let available = decode_locator(&raw_group[6..10]);
        for (role, locator) in [("owned", owned), ("available", available)] {
            validate_locator(ordinal, role, locator, geometry)?;
        }
        maps.push(LongValueMapDefinition {
            column: ColumnOrdinal::new(ordinal),
            owned,
            available,
            raw_group,
        });
        seen[usize::from(ordinal)] = true;
    }
    if let Some(missing) = columns
        .iter()
        .filter(|column| is_long_value(column))
        .find(|column| !seen[usize::from(column.ordinal().get())])
    {
        return Err(LongValueMapError::MissingColumn {
            ordinal: missing.ordinal().get(),
        });
    }
    Ok(maps)
}

fn is_long_value(column: &ColumnDefinition) -> bool {
    matches!(
        column.physical_type(),
        ColumnPhysicalType::Memo | ColumnPhysicalType::LongBinary
    )
}

fn decode_locator(raw: &[u8]) -> MapRowLocator {
    let page = u32::from_le_bytes([raw[1], raw[2], raw[3], 0]);
    MapRowLocator::new(PageNumber::new(u64::from(page)), raw[0])
}

fn validate_locator(
    ordinal: u16,
    role: &'static str,
    locator: MapRowLocator,
    geometry: PageGeometry,
) -> Result<(), LongValueMapError> {
    if locator.page().get() == 0 {
        return Err(LongValueMapError::InvalidReference {
            ordinal,
            role,
            locator,
            source: None,
        });
    }
    geometry
        .validate_reference(locator.page())
        .map_err(|source| LongValueMapError::InvalidReference {
            ordinal,
            role,
            locator,
            source: Some(source),
        })
}
