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
    maps: Vec<crate::mutation_map_write::PendingMap>,
}

impl PageEdits {
    pub fn new(page_count: u64) -> Self {
        Self {
            first_append: page_count,
            changes: Vec::new(),
            append: Vec::new(),
            maps: Vec::new(),
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

    /// Delays map storage growth until row, payload and index placements are fixed.
    pub fn map_bit(
        &mut self,
        database: &mut DatabaseReader<FileSource>,
        locator: MapRowLocator,
        member: PageNumber,
        expected: bool,
        desired: bool,
        budget: &mut ResourceBudget,
    ) -> Result<(), UpdateError> {
        budget.charge_work_units(self.maps.len() as u64)?;
        let position =
            if let Some(position) = self.maps.iter().position(|map| map.bits.locator == locator) {
                position
            } else {
                let bits = crate::mutation_map::MapBits::load(database, locator, budget)?;
                for previous in &self.maps {
                    if bits.overlaps(&previous.bits, budget)? {
                        return Err(UpdateError::Mismatch("aliased mutation allocation maps"));
                    }
                }
                reserve(&mut self.maps, 1, budget)?;
                self.maps
                    .push(crate::mutation_map_write::PendingMap::new(bits));
                self.maps.len() - 1
            };
        self.maps[position].change(member, expected, desired, self.first_append, budget)
    }

    pub(crate) fn map_record(
        &mut self,
        database: &mut DatabaseReader<FileSource>,
        locator: MapRowLocator,
        expected: &[u8],
        desired: &[u8],
        budget: &mut ResourceBudget,
    ) -> Result<(), UpdateError> {
        let mut before = [0; PAGE_BYTES];
        database.read_raw_page(locator.page(), &mut before, budget)?;
        budget.charge_work_units(self.changes.len() as u64)?;
        let mut current = self
            .changes
            .iter()
            .find(|change| change.page == locator.page())
            .map_or_else(
                || PageImage::from_bytes(before),
                |change| change.after.clone(),
            );
        let page =
            crate::classify_page(locator.page(), current.as_bytes(), budget).map_err(|error| {
                UpdateError::Definition(crate::TableDefinitionError::Page(
                    crate::DatabasePageError::Classification(error),
                ))
            })?;
        let record =
            crate::locate_usage_map(page, locator, budget).map_err(UpdateError::UsageMap)?;
        if record.raw() != expected {
            return Err(UpdateError::Mismatch(
                "allocation record changed during staging",
            ));
        }
        let range = record.range();
        if expected.len() == desired.len() {
            current.write_at(PageOffset::from_usize(range.start)?, desired, budget)?;
        } else {
            // SRC-0020 supplies the data-page owner field; EXP-0162 supplies row movement.
            let bytes = current.as_bytes();
            let owner = PageNumber::new(u64::from(u32::from_le_bytes([
                bytes[4], bytes[5], bytes[6], bytes[7],
            ])));
            current = crate::row_update_page::replace(
                locator.page(),
                owner,
                bytes,
                locator.row(),
                desired,
                budget,
            )?;
        }
        if let Some(change) = self
            .changes
            .iter_mut()
            .find(|change| change.page == locator.page())
        {
            change.after = current;
        } else {
            self.replace(
                PageChange {
                    page: locator.page(),
                    before: &before,
                    after: current.as_bytes(),
                },
                budget,
            )?;
        }
        Ok(())
    }

    pub(crate) fn patch_bytes(
        &mut self,
        database: &mut DatabaseReader<FileSource>,
        page: PageNumber,
        offset: usize,
        bytes: &[u8],
        budget: &mut ResourceBudget,
    ) -> Result<(), UpdateError> {
        if page.get() >= self.first_append {
            let ordinal = usize::try_from(page.get() - self.first_append)
                .map_err(|_| UpdateError::Mismatch("bitmap append ordinal"))?;
            self.append
                .get_mut(ordinal)
                .ok_or(UpdateError::Mismatch("bitmap append page"))?
                .write_at(PageOffset::from_usize(offset)?, bytes, budget)?;
            return Ok(());
        }
        budget.charge_work_units(self.changes.len() as u64)?;
        if let Some(change) = self.changes.iter_mut().find(|change| change.page == page) {
            let end = offset
                .checked_add(bytes.len())
                .filter(|end| *end <= PAGE_BYTES)
                .ok_or(UpdateError::Mismatch("map patch bounds"))?;
            for (at, value) in (offset..end).zip(bytes) {
                if change.after.as_bytes()[at] != change.before[at]
                    && change.after.as_bytes()[at] != *value
                {
                    return Err(UpdateError::Mismatch(
                        "allocation patch overlaps content edit",
                    ));
                }
            }
            change
                .after
                .write_at(PageOffset::from_usize(offset)?, bytes, budget)?;
        } else {
            let mut before = [0; PAGE_BYTES];
            database.read_raw_page(page, &mut before, budget)?;
            let mut after = PageImage::from_bytes(before);
            after.write_at(PageOffset::from_usize(offset)?, bytes, budget)?;
            self.replace(
                PageChange {
                    page,
                    before: &before,
                    after: after.as_bytes(),
                },
                budget,
            )?;
        }
        Ok(())
    }

    fn finish_maps(
        &mut self,
        database: &mut DatabaseReader<FileSource>,
        budget: &mut ResourceBudget,
    ) -> Result<(), UpdateError> {
        if self.maps.is_empty() {
            return Ok(());
        }
        crate::mutation_map_guard::validate(database, &self.maps, budget)?;
        let mut maps = std::mem::take(&mut self.maps);
        let global_position = maps.iter().position(|map| map.is_global());
        let global = if let Some(position) = global_position {
            maps.remove(position)
        } else {
            crate::mutation_map_write::PendingMap::new(crate::mutation_map::MapBits::load(
                database,
                crate::mutation_map_write::global_locator(),
                budget,
            )?)
        };
        for map in maps {
            map.apply(database, self, budget)?;
        }
        global.apply(database, self, budget)
    }

    pub fn publish<H, HE>(
        mut self,
        path: &Path,
        mut database: DatabaseReader<FileSource>,
        budget: &mut ResourceBudget,
        hook: H,
    ) -> Result<(), UpdateError>
    where
        H: FnMut(PublishStage) -> Result<(), HE>,
        HE: StdError + Send + Sync + 'static,
    {
        self.finish_maps(&mut database, budget)?;
        let source = database.into_source();
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

#[path = "page_edits_sequence.rs"]
mod sequence;
