//! EXP-0059/0247 definition chains and their complete physical page inventory.
use super::table::*;
use crate::{
    ByteCount, DatabaseReader, Error, PageChainWalker, PageKind, PageNumber, ReadAt,
    ResourceBudget, TableMapLocations, locate_table_maps,
};

pub(super) fn read_definition_chain<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    root: PageNumber,
    budget: &mut ResourceBudget,
) -> Result<(Vec<u8>, TableMapLocations, Vec<PageNumber>), TableDefinitionError> {
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
    let mut pages = Vec::new();
    crate::format::resource::reserve(&mut pages, 1, budget)
        .map_err(TableDefinitionError::Resource)?;
    pages.push(root);
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
        crate::format::resource::reserve(&mut pages, 1, budget)
            .map_err(TableDefinitionError::Resource)?;
        pages.push(current);
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
    // EXP-0247: an exact payload boundary may retain one empty terminal
    // definition page. Its payload is slack; the link and prefix remain checked.
    if next.get() != 0
        && length >= PAGE_BYTES
        && (length - PAGE_BYTES).is_multiple_of(PAGE_BYTES - CONTINUATION_PAYLOAD_OFFSET)
    {
        current = next;
        crate::format::resource::reserve(&mut pages, 1, budget)
            .map_err(TableDefinitionError::Resource)?;
        pages.push(current);
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
        next = PageNumber::new(u64::from(u32_at(&page, 4)));
    }
    if next.get() != 0 {
        return Err(TableDefinitionError::TrailingChainReference {
            page: current,
            next,
        });
    }
    Ok((bytes, maps, pages))
}
