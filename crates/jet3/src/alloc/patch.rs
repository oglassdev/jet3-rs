//! Exact allocation-map membership patches from SRC-0020, EXP-0051/0057/0065/0162.
use crate::{
    DatabaseReader, FileSource, MapRowLocator, PageNumber, ResourceBudget, TableDefinition,
    WriteError, alloc::mutation_map::MapBits,
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
        edits: &mut crate::write::page_edits::PageEdits,
        budget: &mut ResourceBudget,
    ) -> Result<(), WriteError> {
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
) -> Result<MapPatches, WriteError> {
    let (expected, desired) = match change {
        AllocationChange::Allocate { available } => {
            ([true, false, false], [false, true, available])
        }
        AllocationChange::Release { available } => ([false, true, available], [true, false, false]),
        AllocationChange::Retain { before, available } => {
            ([false, true, before], [false, true, available])
        }
    };
    let locators = [
        crate::alloc::mutation_map::global_locator(),
        definition.maps().owned(),
        definition.maps().available(),
    ];
    let mut maps = Vec::new();
    crate::write::page_edits::reserve(&mut maps, locators.len(), budget)?;
    for (role, locator) in locators.iter().copied().enumerate() {
        if locator.page() == definition.root()
            || locator.page() == page
            || locators[..role].contains(&locator)
        {
            return Err(WriteError::Mismatch(
                "overlapping allocation map references",
            ));
        }
        let map = MapBits::load(database, locator, budget)?;
        for previous in &maps {
            if map.overlaps(previous, budget)? {
                return Err(WriteError::Mismatch("overlapping allocation map storage"));
            }
        }
        let present =
            if role == 0 && page.get() >= database.geometry().page_count() && !map.represents(page)
            {
                true
            } else {
                map.contains(page)?
            };
        if present != expected[role] {
            return Err(WriteError::Mismatch("allocation patch membership mismatch"));
        }
        maps.push(map);
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
) -> Result<bool, WriteError> {
    MapBits::load(database, definition.maps().available(), budget)?.contains(member)
}
