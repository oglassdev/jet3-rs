//! Exact inline-map membership patches from SRC-0020, EXP-0051/0057/0065/0162.
use crate::allocation::{AllocationMapLayout, decode_allocation_map_layout};
use crate::update_pages::PageChange;
use crate::{
    DatabaseReader, FileSource, MapRowLocator, PAGE_BYTES, PageImage, PageNumber, PageOffset,
    ResourceBudget, TableDefinition, UpdateError,
};

struct MapPage {
    page: PageNumber,
    before: [u8; PAGE_BYTES],
    after: PageImage,
}

pub(crate) enum AllocationChange {
    Allocate { available: bool },
    Release,
}

pub(crate) struct MapPatches {
    maps: [MapPage; 3],
    count: usize,
}

impl MapPatches {
    pub fn changes(&self) -> impl ExactSizeIterator<Item = PageChange<'_>> {
        self.maps[..self.count].iter().map(|map| PageChange {
            page: map.page,
            before: &map.before,
            after: map.after.as_bytes(),
        })
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
        AllocationChange::Release => ([false, true, true], [true, false, false]),
    };
    let mut result = MapPatches {
        maps: std::array::from_fn(|_| MapPage {
            page: PageNumber::new(0),
            before: [0; PAGE_BYTES],
            after: PageImage::from_bytes([0; PAGE_BYTES]),
        }),
        count: 0,
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
        let index = match result.maps[..result.count]
            .iter()
            .position(|m| m.page == locator.page())
        {
            Some(index) => index,
            None => {
                let index = result.count;
                let map = &mut result.maps[index];
                map.page = locator.page();
                database.read_raw_page(map.page, &mut map.before, budget)?;
                map.after = PageImage::from_bytes(map.before);
                result.count += 1;
                index
            }
        };
        let map = &mut result.maps[index];
        let classified = database
            .read_classified_page(locator.page(), &mut map.before, budget)
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
        let old = map.before[offset];
        if (old & mask != 0) != expected[role] {
            return Err(UpdateError::Mismatch(
                "allocation patch membership mismatch",
            ));
        }
        let set = desired[role];
        let value = if set { old | mask } else { old & !mask };
        map.after
            .write_at(PageOffset::new(offset as u64), &[value], budget)?;
    }
    Ok(result)
}
