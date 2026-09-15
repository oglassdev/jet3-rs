//! Inline long-value ownership from EXP-0051/0057/0077.
use crate::{
    ByteCount, DatabaseReader, FileSource, MapRowLocator, PAGE_BYTES, PageNumber, ResourceBudget,
    UpdateError,
};

pub(super) struct Bitmap {
    first: PageNumber,
    bits: Vec<u8>,
}

impl Bitmap {
    pub fn load(
        database: &mut DatabaseReader<FileSource>,
        locator: MapRowLocator,
        budget: &mut ResourceBudget,
    ) -> Result<Self, UpdateError> {
        let mut bytes = [0; PAGE_BYTES];
        let page = database
            .read_classified_page(locator.page(), &mut bytes, budget)
            .map_err(crate::TableDefinitionError::Page)?;
        let row = crate::locate_usage_map(page, locator, budget).map_err(UpdateError::UsageMap)?;
        let crate::allocation::AllocationMapLayout::Inline { start_page, bitmap } =
            crate::allocation::decode_allocation_map_layout(row.raw(), budget)
                .map_err(UpdateError::Allocation)?
        else {
            return Err(UpdateError::Unsupported(
                "indirect long-value allocation map",
            ));
        };
        let mut bits = Vec::new();
        crate::page_edits::reserve(&mut bits, bitmap.len(), budget)?;
        budget.charge_work_units(bitmap.len() as u64)?;
        bits.extend_from_slice(&row.raw()[bitmap]);
        Ok(Self {
            first: start_page,
            bits,
        })
    }

    pub fn contains(&self, page: PageNumber) -> Result<bool, UpdateError> {
        let bit = page
            .get()
            .checked_sub(self.first.get())
            .filter(|bit| *bit / 8 < self.bits.len() as u64)
            .ok_or(UpdateError::Unsupported(
                "page outside long-value inline map",
            ))?;
        Ok(self.bits[(bit / 8) as usize] & (1 << (bit % 8)) != 0)
    }

    pub fn existing_pages(
        &self,
        page_count: u64,
        allow_future: bool,
        budget: &mut ResourceBudget,
    ) -> Result<Vec<PageNumber>, UpdateError> {
        let mut pages = Vec::new();
        budget.charge_items(self.bits.len() as u64 * 8)?;
        for (byte, value) in self.bits.iter().copied().enumerate() {
            for bit in 0..8 {
                if value & (1 << bit) == 0 {
                    continue;
                }
                let page = self
                    .first
                    .get()
                    .checked_add(byte as u64 * 8 + bit)
                    .ok_or(UpdateError::Mismatch("long-value map page number"))?;
                if page >= page_count {
                    if allow_future {
                        continue;
                    }
                    return Err(UpdateError::Mismatch("long-value owned page outside file"));
                }
                crate::page_edits::reserve(&mut pages, 1, budget)?;
                pages.push(PageNumber::new(page));
            }
        }
        Ok(pages)
    }
}

pub(super) fn payload_budget(
    length: usize,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    budget.check_decoded_value(ByteCount::new(length as u64))?;
    Ok(())
}
