//! One private publication for data, index and allocation changes.
use crate::update_pages::PageChange;
use crate::{
    ByteCount, DatabaseReader, FileSource, MapRowLocator, PAGE_BYTES, PageImage, PageNumber,
    PageOffset, PublishStage, ResourceBudget, UpdateError,
};
use std::{error::Error as StdError, mem::size_of, path::Path};

pub(crate) fn reserve<T>(
    items: &mut Vec<T>,
    additional: usize,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    let needed = items
        .len()
        .checked_add(additional)
        .ok_or(UpdateError::Mismatch("edit allocation size"))?;
    if needed <= items.capacity() {
        return Ok(());
    }
    let capacity = needed.max(items.capacity().saturating_mul(2));
    let bytes = (capacity - items.capacity())
        .checked_mul(size_of::<T>())
        .ok_or(UpdateError::Mismatch("edit allocation size"))?;
    budget.charge_allocation(ByteCount::new(bytes as u64))?;
    budget.charge_work_units((items.len() as u64).saturating_mul(size_of::<T>() as u64))?;
    items
        .try_reserve_exact(capacity - items.len())
        .map_err(|_| {
            UpdateError::Resource(crate::Error::Io {
                operation: "reserve page edits",
                kind: std::io::ErrorKind::OutOfMemory,
            })
        })?;
    Ok(())
}

struct Change {
    page: PageNumber,
    before: [u8; PAGE_BYTES],
    after: PageImage,
}

pub(crate) struct PageEdits {
    first_append: u64,
    changes: Vec<Change>,
    append: Vec<PageImage>,
}

impl PageEdits {
    pub fn new(page_count: u64) -> Self {
        Self {
            first_append: page_count,
            changes: Vec::new(),
            append: Vec::new(),
        }
    }

    pub fn next_append_page(&self) -> Result<PageNumber, UpdateError> {
        self.first_append
            .checked_add(self.append.len() as u64)
            .filter(|n| *n <= 0x00ff_ffff)
            .map(PageNumber::new)
            .ok_or(UpdateError::Unsupported("appended page reference width"))
    }

    pub fn append(
        &mut self,
        image: PageImage,
        budget: &mut ResourceBudget,
    ) -> Result<PageNumber, UpdateError> {
        let page = self.next_append_page()?;
        // EXP-0062: row locators carry a 24-bit page reference.
        reserve(&mut self.append, 1, budget)?;
        self.append.push(image);
        Ok(page)
    }

    pub fn replace(
        &mut self,
        change: PageChange<'_>,
        budget: &mut ResourceBudget,
    ) -> Result<(), UpdateError> {
        budget.charge_work_units(self.changes.len() as u64 + PAGE_BYTES as u64)?;
        if let Some(existing) = self.changes.iter_mut().find(|c| c.page == change.page) {
            if &existing.before != change.before {
                return Err(UpdateError::Mismatch("inconsistent source page"));
            }
            for offset in 0..PAGE_BYTES {
                if change.before[offset] != change.after[offset] {
                    if existing.after.as_bytes()[offset] != change.before[offset]
                        && existing.after.as_bytes()[offset] != change.after[offset]
                    {
                        return Err(UpdateError::Mismatch("overlapping page edits"));
                    }
                    existing.after.write_at(
                        PageOffset::new(offset as u64),
                        &change.after[offset..offset + 1],
                        budget,
                    )?;
                }
            }
            return Ok(());
        }
        if change.page.get() >= self.first_append {
            return Err(UpdateError::Mismatch("source page outside original"));
        }
        reserve(&mut self.changes, 1, budget)?;
        self.changes.push(Change {
            page: change.page,
            before: *change.before,
            after: PageImage::from_bytes(*change.after),
        });
        Ok(())
    }

    pub fn set_image(
        &mut self,
        database: &mut DatabaseReader<FileSource>,
        page: PageNumber,
        image: PageImage,
        budget: &mut ResourceBudget,
    ) -> Result<(), UpdateError> {
        if page.get() >= self.first_append {
            let ordinal = usize::try_from(page.get() - self.first_append)
                .map_err(|_| UpdateError::Mismatch("append ordinal"))?;
            let target = self
                .append
                .get_mut(ordinal)
                .ok_or(UpdateError::Mismatch("unplanned append"))?;
            *target = image;
        } else {
            let mut before = [0; PAGE_BYTES];
            database.read_raw_page(page, &mut before, budget)?;
            self.replace(
                PageChange {
                    page,
                    before: &before,
                    after: image.as_bytes(),
                },
                budget,
            )?;
        }
        Ok(())
    }

    /// EXP-0051/0057/0065: global free bits and inline index-map membership.
    pub fn map_bit(
        &mut self,
        database: &mut DatabaseReader<FileSource>,
        locator: MapRowLocator,
        member: PageNumber,
        expected: bool,
        desired: bool,
        budget: &mut ResourceBudget,
    ) -> Result<(), UpdateError> {
        let mut before = [0; PAGE_BYTES];
        let page = database
            .read_classified_page(locator.page(), &mut before, budget)
            .map_err(crate::TableDefinitionError::Page)?;
        let row = crate::locate_usage_map(page, locator, budget).map_err(UpdateError::UsageMap)?;
        let crate::allocation::AllocationMapLayout::Inline { start_page, bitmap } =
            crate::allocation::decode_allocation_map_layout(row.raw(), budget)
                .map_err(UpdateError::Allocation)?
        else {
            return Err(UpdateError::Unsupported("indirect index allocation"));
        };
        let bit = member
            .get()
            .checked_sub(start_page.get())
            .filter(|bit| *bit / 8 < bitmap.len() as u64)
            .ok_or(UpdateError::Unsupported(
                "page outside index allocation map",
            ))?;
        let offset = row.range().start + bitmap.start + (bit / 8) as usize;
        let mask = 1 << (bit % 8);
        budget.charge_work_units(self.changes.len() as u64)?;
        // Combine several allocations in the same byte without losing earlier bits.
        let mut after = self
            .changes
            .iter()
            .find(|c| c.page == locator.page())
            .map_or_else(|| PageImage::from_bytes(before), |c| c.after.clone());
        let old = after.as_bytes()[offset];
        if (old & mask != 0) != expected {
            return Err(UpdateError::Mismatch("index map membership"));
        }
        let value = if desired { old | mask } else { old & !mask };
        after.write_at(PageOffset::new(offset as u64), &[value], budget)?;
        if let Some(existing) = self.changes.iter_mut().find(|c| c.page == locator.page()) {
            existing.after = after;
        } else {
            self.replace(
                PageChange {
                    page: locator.page(),
                    before: &before,
                    after: after.as_bytes(),
                },
                budget,
            )?;
        }
        Ok(())
    }

    pub fn publish<H, HE>(
        self,
        path: &Path,
        source: FileSource,
        budget: &mut ResourceBudget,
        hook: H,
    ) -> Result<(), UpdateError>
    where
        H: FnMut(PublishStage) -> Result<(), HE>,
        HE: StdError + Send + Sync + 'static,
    {
        let mut changes = Vec::new();
        reserve(&mut changes, self.changes.len(), budget)?;
        changes.extend(self.changes.iter().map(|c| PageChange {
            page: c.page,
            before: &c.before,
            after: c.after.as_bytes(),
        }));
        crate::update_pages::publish_changes_with_appends(
            path,
            source,
            &changes,
            &self.append,
            budget,
            hook,
        )
    }
}
