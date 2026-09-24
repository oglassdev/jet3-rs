//! EXP-0262 composes logical and hidden-slot edits before changing allocation bits.
use crate::{
    DatabaseReader, FileSource, PAGE_BYTES, PageImage, PageNumber, ResourceBudget, RowLocator,
    TableDefinition, UpdateError,
    row::{data_page::DataPageEditor, directory::RowSlot},
    write::page_edits::{PageEdits, reserve},
};

struct ChangedPage {
    page: PageNumber,
    before: [u8; PAGE_BYTES],
    after: PageImage,
}

pub(crate) struct RowPages {
    pages: Vec<ChangedPage>,
}

impl RowPages {
    pub fn new() -> Self {
        Self { pages: Vec::new() }
    }

    fn position(
        &mut self,
        database: &mut DatabaseReader<FileSource>,
        page: PageNumber,
        budget: &mut ResourceBudget,
    ) -> Result<usize, UpdateError> {
        budget.charge_work_units(self.pages.len() as u64)?;
        if let Some(index) = self.pages.iter().position(|entry| entry.page == page) {
            return Ok(index);
        }
        let mut before = [0; PAGE_BYTES];
        database.read_raw_page(page, &mut before, budget)?;
        reserve(&mut self.pages, 1, budget)?;
        let index = self.pages.len();
        self.pages.push(ChangedPage {
            page,
            before,
            after: PageImage::from_bytes(before),
        });
        Ok(index)
    }

    pub fn replace(
        &mut self,
        database: &mut DatabaseReader<FileSource>,
        owner: PageNumber,
        row: RowLocator,
        encoded: &[u8],
        state: RowSlot,
        budget: &mut ResourceBudget,
    ) -> Result<bool, UpdateError> {
        let index = self.position(database, row.page(), budget)?;
        let entry = &mut self.pages[index];
        let Some(after) = DataPageEditor::open(row.page(), owner, entry.after.as_bytes(), budget)?
            .replace(row.slot(), encoded, Some(state), budget)?
        else {
            return Ok(false);
        };
        entry.after = after;
        Ok(true)
    }

    pub fn remove(
        &mut self,
        database: &mut DatabaseReader<FileSource>,
        owner: PageNumber,
        row: RowLocator,
        budget: &mut ResourceBudget,
    ) -> Result<(), UpdateError> {
        let index = self.position(database, row.page(), budget)?;
        let entry = &mut self.pages[index];
        entry.after = DataPageEditor::open(row.page(), owner, entry.after.as_bytes(), budget)?
            .remove(row.slot(), true, budget)?
            .into_image();
        Ok(())
    }

    pub fn appended(
        &mut self,
        page: PageNumber,
        before: [u8; PAGE_BYTES],
        after: PageImage,
        budget: &mut ResourceBudget,
    ) -> Result<(), UpdateError> {
        budget.charge_work_units(self.pages.len() as u64)?;
        if self.pages.iter().any(|entry| entry.page == page) {
            return Err(UpdateError::Mismatch(
                "row destination overlaps selected chain",
            ));
        }
        reserve(&mut self.pages, 1, budget)?;
        self.pages.push(ChangedPage {
            page,
            before,
            after,
        });
        Ok(())
    }

    pub fn stage(
        self,
        database: &mut DatabaseReader<FileSource>,
        definition: &TableDefinition,
        edits: &mut PageEdits,
        budget: &mut ResourceBudget,
    ) -> Result<(), UpdateError> {
        let minimum = crate::row::insert_page::minimum_length(definition.columns(), budget)?;
        for entry in self.pages {
            let available =
                crate::alloc::patch::available(database, definition, entry.page, budget)?;
            let change = if entry.after.as_bytes()[0] == 9 {
                crate::alloc::patch::AllocationChange::Release { available }
            } else {
                crate::alloc::patch::AllocationChange::Retain {
                    before: available,
                    available: crate::row::data_page::has_capacity(entry.after.as_bytes(), minimum),
                }
            };
            let maps = crate::alloc::patch::plan(database, definition, entry.page, change, budget)?;
            maps.stage(database, edits, budget)?;
            edits.replace(
                crate::write::update_pages::PageChange {
                    page: entry.page,
                    before: &entry.before,
                    after: entry.after.as_bytes(),
                },
                budget,
            )?;
        }
        Ok(())
    }
}
