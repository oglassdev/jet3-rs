//! Single EOF allocation: SRC-0020/EXP-0057 map framing and EXP-0051 free bits;
//! EXP-0065 Q2 clears the new EOF bit. Row packing uses EXP-0060/EXP-0116.
use crate::allocation_patch::{AllocationChange, MapPatches};
use crate::update_pages::PageChange;
use crate::{
    DatabaseReader, FileSource, PageImage, PageImageError, PageNumber, PageOffset, ResourceBudget,
    TableDefinition, UpdateError,
};

pub(crate) struct EofInsert {
    pub page: PageNumber,
    pub image: PageImage,
    maps: MapPatches,
}

impl EofInsert {
    pub fn changes<'a>(&'a self, definition: PageChange<'a>) -> ([PageChange<'a>; 4], usize) {
        let mut changes = [definition; 4];
        let maps = self.maps.changes();
        let count = maps.len();
        for (change, map) in changes.iter_mut().skip(1).zip(maps) {
            *change = map;
        }
        (changes, count + 1)
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
    let maps = crate::allocation_patch::plan(
        database,
        definition,
        page,
        AllocationChange::Allocate { available },
        budget,
    )?;
    Ok(EofInsert { page, image, maps })
}
