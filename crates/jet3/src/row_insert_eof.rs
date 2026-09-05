//! Single EOF allocation: SRC-0020/EXP-0057 map framing and EXP-0051 free bits;
//! EXP-0065 Q2 clears the new EOF bit. Row packing uses EXP-0060/EXP-0116.
use crate::allocation::{AllocationMapLayout, decode_allocation_map_layout};
use crate::update_pages::PageChange;
use crate::{
    DatabaseReader, FileSource, MapRowLocator, PAGE_BYTES, PageImage, PageImageError, PageNumber,
    PageOffset, ResourceBudget, TableDefinition, UpdateError,
};

struct MapPage {
    page: PageNumber,
    before: [u8; PAGE_BYTES],
    after: PageImage,
}

pub(crate) struct EofInsert {
    pub page: PageNumber,
    pub image: PageImage,
    maps: [MapPage; 3],
    count: usize,
}

impl EofInsert {
    pub fn changes<'a>(&'a self, definition: PageChange<'a>) -> ([PageChange<'a>; 4], usize) {
        let mut changes = [definition; 4];
        for (change, map) in changes.iter_mut().skip(1).zip(&self.maps[..self.count]) {
            *change = PageChange {
                page: map.page,
                before: &map.before,
                after: map.after.as_bytes(),
            };
        }
        (changes, self.count + 1)
    }
}

fn page_error(error: PageImageError) -> UpdateError {
    match error {
        PageImageError::Encoding(error) => UpdateError::Resource(error),
        _ => UpdateError::Unsupported("row or owner does not fit new data page"),
    }
}

pub(crate) fn plan(
    database: &mut DatabaseReader<FileSource>,
    definition: &TableDefinition,
    encoded: &[u8],
    minimum: &[u8],
    budget: &mut ResourceBudget,
) -> Result<EofInsert, UpdateError> {
    let page = PageNumber::new(database.geometry().page_count());
    // Map references to data pages must remain representable by Jet's u24 locators.
    if page.get() > 0x00ff_ffff {
        return Err(UpdateError::Unsupported("EOF page reference width"));
    }
    let mut builder = crate::DataPageBuilder::new(definition.root(), budget).map_err(page_error)?;
    builder.append_row(encoded, budget).map_err(page_error)?;
    let free = u16::try_from(builder.free_bytes().get())
        .map_err(|_| UpdateError::Mismatch("new data page free bytes"))?;
    // The same candidate policy as initial creation: physically fit a minimum row.
    let available = match builder.clone().append_row(minimum, budget) {
        Ok(_) => true,
        Err(PageImageError::PageFull { .. } | PageImageError::RowSlotsExhausted { .. }) => false,
        Err(error) => return Err(page_error(error)),
    };
    let mut image = builder.finish();
    let [lo, hi] = free.to_le_bytes();
    image.write_at(PageOffset::new(1), &[1, lo, hi], budget)?;
    let mut result = EofInsert {
        page,
        image,
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
        if locator.page() == definition.root() || locators[..role].contains(&locator) {
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
            return Err(UpdateError::Unsupported("indirect EOF allocation map"));
        };
        let bit = page
            .get()
            .checked_sub(start_page.get())
            .filter(|bit| *bit / 8 < bitmap.len() as u64)
            .ok_or(UpdateError::Unsupported("EOF outside existing inline map"))?;
        let offset = range.start + bitmap.start + (bit / 8) as usize;
        let mask = 1_u8 << (bit % 8);
        let old = map.before[offset];
        if (old & mask != 0) != (role == 0) {
            return Err(UpdateError::Mismatch("EOF page already in use or owned"));
        }
        let set = role == 1 || (role == 2 && available);
        let value = if set { old | mask } else { old & !mask };
        map.after
            .write_at(PageOffset::new(offset as u64), &[value], budget)?;
    }
    Ok(result)
}
