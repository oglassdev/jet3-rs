//! Exact inline-map membership patches from SRC-0020, EXP-0051/0057/0065/0162.
use crate::allocation::{AllocationMapLayout, decode_allocation_map_layout};
use crate::{
    DatabaseReader, FileSource, MapRowLocator, PAGE_BYTES, PageNumber, ResourceBudget,
    TableDefinition, UpdateError,
};

pub(crate) enum AllocationChange {
    Allocate { available: bool },
    Release { available: bool },
    Retain { before: bool, available: bool },
}

pub(crate) struct MapPatches {
    locators: [MapRowLocator; 3],
    member: PageNumber,
    expected: [bool; 3],
    desired: [bool; 3],
}

impl MapPatches {
    pub fn stage(
        &self,
        database: &mut DatabaseReader<FileSource>,
        edits: &mut crate::page_edits::PageEdits,
        budget: &mut ResourceBudget,
    ) -> Result<(), UpdateError> {
        for role in 0..self.locators.len() {
            edits.map_bit(
                database,
                self.locators[role],
                self.member,
                self.expected[role],
                self.desired[role],
                budget,
            )?;
        }
        Ok(())
    }
}

pub(crate) fn plan(
    database: &mut DatabaseReader<FileSource>,
    definition: &TableDefinition,
    page: PageNumber,
    change: AllocationChange,
    budget: &mut ResourceBudget,
) -> Result<MapPatches, UpdateError> {
    let (expected, desired) = match change {
        AllocationChange::Allocate { available } => {
            ([true, false, false], [false, true, available])
        }
        AllocationChange::Release { available } => ([false, true, available], [true, false, false]),
        AllocationChange::Retain { before, available } => {
            ([false, true, before], [false, true, available])
        }
    };
    // EXP-0051 identifies the global free-page map at page 1, row 0.
    let locators = [
        MapRowLocator::new(PageNumber::new(1), 0),
        definition.maps().owned(),
        definition.maps().available(),
    ];
    let mut ranges = std::array::from_fn::<_, 3, _>(|_| 0..0);
    for (role, locator) in locators.iter().copied().enumerate() {
        if locator.page() == definition.root()
            || locator.page() == page
            || locators[..role].contains(&locator)
        {
            return Err(UpdateError::Mismatch(
                "overlapping allocation map references",
            ));
        }
        let mut bytes = [0; PAGE_BYTES];
        let classified = database
            .read_classified_page(locator.page(), &mut bytes, budget)
            .map_err(crate::TableDefinitionError::Page)?;
        let record =
            crate::locate_usage_map(classified, locator, budget).map_err(UpdateError::UsageMap)?;
        let range = record.range();
        if (0..role).any(|prior| {
            locators[prior].page() == locator.page()
                && range.start < ranges[prior].end
                && ranges[prior].start < range.end
        }) {
            return Err(UpdateError::Mismatch("overlapping allocation map records"));
        }
        ranges[role] = range.clone();
        let AllocationMapLayout::Inline { start_page, bitmap } =
            decode_allocation_map_layout(record.raw(), budget).map_err(UpdateError::Allocation)?
        else {
            return Err(UpdateError::Unsupported("indirect allocation patch map"));
        };
        let bit = page
            .get()
            .checked_sub(start_page.get())
            .filter(|bit| *bit / 8 < bitmap.len() as u64)
            .ok_or(UpdateError::Unsupported("page outside existing inline map"))?;
        let offset = range.start + bitmap.start + (bit / 8) as usize;
        let mask = 1_u8 << (bit % 8);
        let old = bytes[offset];
        if (old & mask != 0) != expected[role] {
            return Err(UpdateError::Mismatch(
                "allocation patch membership mismatch",
            ));
        }
    }
    Ok(MapPatches {
        locators,
        member: page,
        expected,
        desired,
    })
}

/// Reads membership without treating the available map as an ownership requirement.
pub(crate) fn available(
    database: &mut DatabaseReader<FileSource>,
    definition: &TableDefinition,
    member: PageNumber,
    budget: &mut ResourceBudget,
) -> Result<bool, UpdateError> {
    let locator = definition.maps().available();
    let mut bytes = [0; PAGE_BYTES];
    let page = database
        .read_classified_page(locator.page(), &mut bytes, budget)
        .map_err(crate::TableDefinitionError::Page)?;
    let row = crate::locate_usage_map(page, locator, budget).map_err(UpdateError::UsageMap)?;
    let AllocationMapLayout::Inline { start_page, bitmap } =
        decode_allocation_map_layout(row.raw(), budget).map_err(UpdateError::Allocation)?
    else {
        return Err(UpdateError::Unsupported("indirect available map"));
    };
    let bit = member
        .get()
        .checked_sub(start_page.get())
        .filter(|bit| *bit / 8 < bitmap.len() as u64)
        .ok_or(UpdateError::Unsupported("page outside existing inline map"))?;
    Ok(row.raw()[bitmap.start + (bit / 8) as usize] & (1 << (bit % 8)) != 0)
}
