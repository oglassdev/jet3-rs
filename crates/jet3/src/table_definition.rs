//! Bounded immutable Jet 3 table definitions from `EXP-0059`, composed with
//! the table allocation-map locators from `EXP-0057`.
//!
//! Definitions retain sourced bytes and typed references. Callers can pass a
//! decoded definition to [`crate::DatabaseReader::index_tree`] separately.

use std::fmt;

use crate::column_definition::{ColumnDefinition, ColumnPhysicalType, decode_columns};
use crate::index_definition::{
    DecodedIndexes, IndexDecodeContext, IndexDefinition, IndexDefinitionError, IndexDefinitionKind,
    PhysicalIndexDefinition, decode_indexes,
};
use crate::{
    AllocationTraversalError, ByteCount, DatabasePageError, DatabaseReader, Error, JET3_PAGE_SIZE,
    MapLocationError, PageChainWalker, PageKind, PageNumber, ReadAt, ResourceBudget,
    TableMapLocations, locate_table_maps,
};

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const CONTINUATION_PAYLOAD_OFFSET: usize = 8;
const DEFINITION_HEADER_LEN: usize = 43;
const PHYSICAL_PREFIX_LEN: usize = 8;
const TERMINATOR_LEN: usize = 2;
const DEFINITION_PREFIX: [u8; 4] = [0x02, 0x01, 0x56, 0x43];
const HEADER_MARKER: u8 = 0x4e;

/// One complete immutable table definition.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TableDefinition {
    root: PageNumber,
    logical_length: u32,
    maps: TableMapLocations,
    columns: Vec<ColumnDefinition>,
    physical_indexes: Vec<PhysicalIndexDefinition>,
    indexes: Vec<IndexDefinition>,
    raw_header: [u8; DEFINITION_HEADER_LEN],
    raw_suffix: Vec<u8>,
}

impl TableDefinition {
    #[must_use]
    /// Returns the table-definition root page.
    pub const fn root(&self) -> PageNumber {
        self.root
    }

    #[must_use]
    /// Returns the declared logical definition length.
    pub const fn logical_length(&self) -> u32 {
        self.logical_length
    }

    #[must_use]
    /// Returns the table's free-space and owned-page map locations.
    pub const fn maps(&self) -> TableMapLocations {
        self.maps
    }

    #[must_use]
    /// Returns physical column definitions in ordinal order.
    pub fn columns(&self) -> &[ColumnDefinition] {
        &self.columns
    }

    #[must_use]
    /// Returns decoded physical index records.
    pub fn physical_indexes(&self) -> &[PhysicalIndexDefinition] {
        &self.physical_indexes
    }

    #[must_use]
    /// Returns named logical index definitions.
    pub fn indexes(&self) -> &[IndexDefinition] {
        &self.indexes
    }

    #[must_use]
    /// Returns the complete sourced definition header.
    pub const fn raw_header(&self) -> &[u8; DEFINITION_HEADER_LEN] {
        &self.raw_header
    }

    /// Returns still-uninterpreted sourced bytes before the final `ff ff`.
    #[must_use]
    pub fn raw_suffix(&self) -> &[u8] {
        &self.raw_suffix
    }
}

/// Structured failure while following or decoding a table definition.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum TableDefinitionError {
    /// Continuation-chain traversal failed.
    Chain(AllocationTraversalError),
    /// Allocation-map location decoding failed.
    MapLocation(MapLocationError),
    /// Reading or classifying a referenced page failed.
    Page(DatabasePageError),
    /// Physical or logical index decoding failed.
    Index(IndexDefinitionError),
    /// The root page lacks the observed table-definition prefix.
    InvalidPrefix {
        /// Root page being decoded.
        page: PageNumber,
        /// Sourced four-byte prefix.
        actual: [u8; 4],
    },
    /// The declared definition length is outside supported bounds.
    InvalidLogicalLength {
        /// Sourced logical length.
        length: u32,
        /// Minimum complete definition length.
        minimum: usize,
        /// Maximum bytes available from captured geometry.
        maximum: u64,
    },
    /// The continuation chain ended before the declared length.
    TruncatedChain {
        /// Last page read from the chain.
        page: PageNumber,
        /// Definition bytes still required.
        remaining: usize,
    },
    /// A chain link remains after the declared definition length.
    TrailingChainReference {
        /// Page containing the trailing link.
        page: PageNumber,
        /// Unexpected next page.
        next: PageNumber,
    },
    /// The table header marker differs from the observed constant.
    InvalidHeaderMarker {
        /// Sourced marker byte.
        raw: u8,
    },
    /// Repeated header column counts disagree.
    InconsistentColumnCount {
        /// First sourced count.
        first: u16,
        /// Repeated sourced count.
        repeated: u16,
    },
    /// A reserved header count is nonzero.
    UnsupportedReservedCount {
        /// Sourced reserved count.
        raw: u16,
    },
    /// The definition ended before a complete field.
    Truncated {
        /// Byte offset where decoding stopped.
        offset: usize,
        /// Additional bytes required.
        needed: usize,
        /// Available definition length.
        length: usize,
    },
    /// A column record's ordinal fields disagree with its position.
    InvalidColumnOrdinal {
        /// Zero-based record ordinal.
        record: u16,
        /// First sourced ordinal.
        first: u16,
        /// Repeated sourced ordinal.
        repeated: u16,
    },
    /// A column record's running variable counter is inconsistent.
    InvalidVariableCounter {
        /// Zero-based column ordinal.
        ordinal: u16,
        /// Sourced counter.
        raw: u16,
        /// Counter derived from preceding columns.
        expected: u16,
    },
    /// The decoded variable-column count differs from the header.
    InconsistentVariableCount {
        /// Sourced header count.
        header: u16,
        /// Count derived from decoded columns.
        decoded: u16,
    },
    /// A column physical-type byte is outside the admitted inventory.
    UnsupportedPhysicalType {
        /// Zero-based column ordinal.
        ordinal: u16,
        /// Sourced physical-type byte.
        raw: u8,
    },
    /// A column storage-class byte is incompatible with its type.
    UnsupportedColumnClass {
        /// Zero-based column ordinal.
        ordinal: u16,
        /// Decoded physical type.
        physical_type: ColumnPhysicalType,
        /// Sourced class byte.
        raw: u8,
    },
    /// A column size is incompatible with its physical type.
    UnsupportedColumnSize {
        /// Zero-based column ordinal.
        ordinal: u16,
        /// Decoded physical type.
        physical_type: ColumnPhysicalType,
        /// Sourced size.
        size: u16,
    },
    /// A fixed column does not begin at the next derived offset.
    InvalidFixedOffset {
        /// Zero-based column ordinal.
        ordinal: u16,
        /// Sourced fixed offset.
        raw: u16,
        /// Offset derived from preceding fixed columns.
        expected: u16,
    },
    /// A sourced column constant differs from the observed value.
    InvalidColumnConstant {
        /// Zero-based column ordinal.
        ordinal: u16,
        /// Sourced constant.
        raw: u16,
    },
    /// A column's name-encoding context differs from the observed bytes.
    InvalidColumnEncodingContext {
        /// Zero-based column ordinal.
        ordinal: u16,
        /// Sourced context bytes.
        raw: [u8; 4],
    },
    /// A column name is empty or duplicates an earlier name.
    InvalidColumnName {
        /// Zero-based column ordinal.
        ordinal: u16,
        /// Whether the name duplicates an earlier column name.
        duplicate: bool,
    },
    /// The definition does not end with the observed terminator.
    InvalidTerminator {
        /// Terminator byte offset.
        offset: usize,
        /// Sourced terminator bytes.
        actual: [u8; 2],
    },
    /// A typed page reference has the wrong classification.
    UnexpectedReferenceKind {
        /// Semantic role of the reference.
        role: &'static str,
        /// Referenced page.
        page: PageNumber,
        /// Observed page classification.
        actual: PageKind,
    },
    /// Resource policy rejected decoding work or owned storage.
    Resource(Error),
}

impl fmt::Display for TableDefinitionError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "table definition failed: {self:?}")
    }
}

impl std::error::Error for TableDefinitionError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Chain(source) => Some(source),
            Self::MapLocation(source) => Some(source),
            Self::Page(source) => Some(source),
            Self::Index(source) => Some(source),
            Self::Resource(source) => Some(source),
            _ => None,
        }
    }
}

impl<S: ReadAt> DatabaseReader<S> {
    /// Reads one catalog-referenced table definition under the same operation budget.
    pub fn table_definition(
        &mut self,
        root: PageNumber,
        budget: &mut ResourceBudget,
    ) -> Result<TableDefinition, TableDefinitionError> {
        let geometry = self.geometry();
        let (bytes, maps) = read_definition_chain(self, root, budget)?;
        let mut definition = decode_definition(&bytes, root, maps, geometry, budget)?;
        validate_index_references(self, &definition, budget)?;
        definition.logical_length = u32::try_from(bytes.len()).map_err(|_| {
            TableDefinitionError::Resource(Error::IntegerConversion {
                value: bytes.len() as u128,
                target: "u32",
            })
        })?;
        Ok(definition)
    }
}

fn read_definition_chain<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    root: PageNumber,
    budget: &mut ResourceBudget,
) -> Result<(Vec<u8>, TableMapLocations), TableDefinitionError> {
    let geometry = database.geometry();
    let maximum = geometry
        .page_count()
        .saturating_sub(1)
        .checked_mul((PAGE_BYTES - CONTINUATION_PAYLOAD_OFFSET) as u64)
        .and_then(|tail| tail.checked_add(PAGE_BYTES as u64))
        .ok_or(TableDefinitionError::Resource(Error::Arithmetic {
            operation: "bound table-definition chain capacity",
        }))?;
    let mut walker = PageChainWalker::new(geometry, budget).map_err(TableDefinitionError::Chain)?;
    let mut page = [0_u8; PAGE_BYTES];
    let classified = walker
        .follow(root, PageKind::TableDefinition, database, &mut page, budget)
        .map_err(TableDefinitionError::Chain)?;
    validate_prefix(root, classified.raw_bytes())?;
    let maps = locate_table_maps(classified, geometry, budget)
        .map_err(TableDefinitionError::MapLocation)?;
    let logical_length = u32_at(&page, 8);
    let minimum = DEFINITION_HEADER_LEN + TERMINATOR_LEN;
    if usize::try_from(logical_length)
        .ok()
        .is_none_or(|length| length < minimum)
        || u64::from(logical_length) > maximum
    {
        return Err(TableDefinitionError::InvalidLogicalLength {
            length: logical_length,
            minimum,
            maximum,
        });
    }
    let length = usize::try_from(logical_length).map_err(|_| {
        TableDefinitionError::Resource(Error::IntegerConversion {
            value: u128::from(logical_length),
            target: "usize",
        })
    })?;
    budget
        .charge_allocation(ByteCount::new(u64::from(logical_length)))
        .map_err(TableDefinitionError::Resource)?;
    let mut bytes = Vec::new();
    bytes.try_reserve_exact(length).map_err(|_| {
        TableDefinitionError::Resource(Error::Io {
            operation: "reserve logical table definition",
            kind: std::io::ErrorKind::OutOfMemory,
        })
    })?;
    let root_bytes = length.min(PAGE_BYTES);
    bytes.extend_from_slice(&page[..root_bytes]);
    let mut current = root;
    let mut next = PageNumber::new(u64::from(u32_at(&page, 4)));
    while bytes.len() < length {
        if next.get() == 0 {
            return Err(TableDefinitionError::TruncatedChain {
                page: current,
                remaining: length - bytes.len(),
            });
        }
        current = next;
        walker
            .follow(
                current,
                PageKind::TableDefinition,
                database,
                &mut page,
                budget,
            )
            .map_err(TableDefinitionError::Chain)?;
        validate_prefix(current, &page)?;
        let count = (length - bytes.len()).min(PAGE_BYTES - CONTINUATION_PAYLOAD_OFFSET);
        bytes.extend_from_slice(
            &page[CONTINUATION_PAYLOAD_OFFSET..CONTINUATION_PAYLOAD_OFFSET + count],
        );
        next = PageNumber::new(u64::from(u32_at(&page, 4)));
    }
    if next.get() != 0 {
        return Err(TableDefinitionError::TrailingChainReference {
            page: current,
            next,
        });
    }
    Ok((bytes, maps))
}

fn decode_definition(
    bytes: &[u8],
    root: PageNumber,
    maps: TableMapLocations,
    geometry: crate::PageGeometry,
    budget: &mut ResourceBudget,
) -> Result<TableDefinition, TableDefinitionError> {
    let raw_header = array_at::<DEFINITION_HEADER_LEN>(bytes, 0)?;
    if raw_header[20] != HEADER_MARKER {
        return Err(TableDefinitionError::InvalidHeaderMarker {
            raw: raw_header[20],
        });
    }
    let column_count = u16_at(bytes, 21);
    let variable_count = u16_at(bytes, 23);
    let repeated_column_count = u16_at(bytes, 25);
    if column_count != repeated_column_count {
        return Err(TableDefinitionError::InconsistentColumnCount {
            first: column_count,
            repeated: repeated_column_count,
        });
    }
    let logical_count = u16_at(bytes, 27);
    let reserved_count = u16_at(bytes, 29);
    if reserved_count != 0 {
        return Err(TableDefinitionError::UnsupportedReservedCount {
            raw: reserved_count,
        });
    }
    let physical_count = u16_at(bytes, 31);
    let prefix_offset = DEFINITION_HEADER_LEN;
    let prefix_bytes = usize::from(physical_count)
        .checked_mul(PHYSICAL_PREFIX_LEN)
        .ok_or(TableDefinitionError::Resource(Error::Arithmetic {
            operation: "size physical index prefixes",
        }))?;
    let mut offset =
        prefix_offset
            .checked_add(prefix_bytes)
            .ok_or(TableDefinitionError::Resource(Error::Arithmetic {
                operation: "locate column records",
            }))?;
    checked_slice(bytes, prefix_offset, prefix_bytes)?;
    let columns = decode_columns(bytes, &mut offset, column_count, variable_count, budget)?;
    let DecodedIndexes { physical, logical } = decode_indexes(
        IndexDecodeContext {
            bytes,
            offset: &mut offset,
            prefix_offset,
            column_count,
            logical_count,
            physical_count,
            table_root: root,
            geometry,
        },
        budget,
    )
    .map_err(TableDefinitionError::Index)?;
    let suffix_end = bytes.len().checked_sub(TERMINATOR_LEN).ok_or(
        TableDefinitionError::InvalidLogicalLength {
            length: u32::try_from(bytes.len()).unwrap_or(u32::MAX),
            minimum: DEFINITION_HEADER_LEN + TERMINATOR_LEN,
            maximum: bytes.len() as u64,
        },
    )?;
    if offset > suffix_end {
        return Err(TableDefinitionError::Truncated {
            offset,
            needed: TERMINATOR_LEN,
            length: bytes.len(),
        });
    }
    let actual = [bytes[suffix_end], bytes[suffix_end + 1]];
    if actual != [0xff, 0xff] {
        return Err(TableDefinitionError::InvalidTerminator {
            offset: suffix_end,
            actual,
        });
    }
    let raw_suffix = copy_owned(&bytes[offset..suffix_end], budget)?;
    Ok(TableDefinition {
        root,
        logical_length: 0,
        maps,
        columns,
        physical_indexes: physical,
        indexes: logical,
        raw_header,
        raw_suffix,
    })
}

fn validate_index_references<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    definition: &TableDefinition,
    budget: &mut ResourceBudget,
) -> Result<(), TableDefinitionError> {
    let mut page = [0_u8; PAGE_BYTES];
    for physical in &definition.physical_indexes {
        validate_reference_kind(
            database,
            physical.usage_map().page(),
            "index usage map",
            |kind| kind == PageKind::Data,
            &mut page,
            budget,
        )?;
        validate_reference_kind(
            database,
            physical.root(),
            "index root",
            |kind| matches!(kind, PageKind::IntermediateIndex | PageKind::LeafIndex),
            &mut page,
            budget,
        )?;
    }
    for index in &definition.indexes {
        if let IndexDefinitionKind::Relationship(reference) = index.kind() {
            validate_reference_kind(
                database,
                reference.related_table(),
                "related table definition",
                |kind| kind == PageKind::TableDefinition,
                &mut page,
                budget,
            )?;
        }
    }
    Ok(())
}

fn validate_reference_kind<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    page_number: PageNumber,
    role: &'static str,
    accepted: impl FnOnce(PageKind) -> bool,
    page: &mut [u8; PAGE_BYTES],
    budget: &mut ResourceBudget,
) -> Result<(), TableDefinitionError> {
    let classified = database
        .read_classified_page(page_number, page, budget)
        .map_err(TableDefinitionError::Page)?;
    if !accepted(classified.kind()) {
        return Err(TableDefinitionError::UnexpectedReferenceKind {
            role,
            page: page_number,
            actual: classified.kind(),
        });
    }
    Ok(())
}

fn validate_prefix(
    page_number: PageNumber,
    page: &[u8; PAGE_BYTES],
) -> Result<(), TableDefinitionError> {
    let actual = [page[0], page[1], page[2], page[3]];
    if actual == DEFINITION_PREFIX {
        Ok(())
    } else {
        Err(TableDefinitionError::InvalidPrefix {
            page: page_number,
            actual,
        })
    }
}

fn copy_owned(bytes: &[u8], budget: &mut ResourceBudget) -> Result<Vec<u8>, TableDefinitionError> {
    budget
        .charge_allocation(
            ByteCount::from_usize(bytes.len()).map_err(TableDefinitionError::Resource)?,
        )
        .map_err(TableDefinitionError::Resource)?;
    let mut owned = Vec::new();
    owned
        .try_reserve_exact(bytes.len())
        .map_err(|_| allocation_failure("reserve raw table-definition suffix"))?;
    owned.extend_from_slice(bytes);
    Ok(owned)
}

fn checked_slice(
    bytes: &[u8],
    offset: usize,
    length: usize,
) -> Result<&[u8], TableDefinitionError> {
    let end = offset
        .checked_add(length)
        .ok_or(TableDefinitionError::Resource(Error::Arithmetic {
            operation: "locate table-definition slice",
        }))?;
    bytes
        .get(offset..end)
        .ok_or(TableDefinitionError::Truncated {
            offset,
            needed: length,
            length: bytes.len(),
        })
}

fn array_at<const N: usize>(bytes: &[u8], offset: usize) -> Result<[u8; N], TableDefinitionError> {
    checked_slice(bytes, offset, N)?
        .try_into()
        .map_err(|_| TableDefinitionError::Truncated {
            offset,
            needed: N,
            length: bytes.len(),
        })
}

fn u16_at(bytes: &[u8], offset: usize) -> u16 {
    u16::from_le_bytes([bytes[offset], bytes[offset + 1]])
}

fn u32_at(bytes: &[u8], offset: usize) -> u32 {
    u32::from_le_bytes([
        bytes[offset],
        bytes[offset + 1],
        bytes[offset + 2],
        bytes[offset + 3],
    ])
}

fn allocation_failure(operation: &'static str) -> TableDefinitionError {
    TableDefinitionError::Resource(Error::Io {
        operation,
        kind: std::io::ErrorKind::OutOfMemory,
    })
}

#[cfg(test)]
#[path = "table_definition_tests.rs"]
mod tests;
