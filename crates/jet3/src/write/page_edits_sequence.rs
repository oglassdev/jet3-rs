//! Accumulate sequential row plans while retaining the original bytes for verification.
use super::page_edits::*;
use crate::{
    ByteCount, DatabaseReader, FileSource, PAGE_BYTES, PageImage, PageNumber, ResourceBudget,
    WriteError, write::update_pages::PageChange,
};
use std::fs::File;
use std::io::{Seek, SeekFrom, Write};

impl PageEdits {
    pub(crate) fn apply_private(
        mut self,
        database: &mut DatabaseReader<FileSource>,
        file: &mut File,
        combined: &mut Self,
        budget: &mut ResourceBudget,
    ) -> Result<(), WriteError> {
        if self.first_append != combined.next_append_page()?.get()
            || file.metadata()?.len() != self.first_append * PAGE_BYTES as u64
        {
            return Err(WriteError::Mismatch("sequential page plan length"));
        }
        self.finish_maps(database, budget)?;
        let final_pages = self.next_append_page()?.get();
        budget
            .read_budget()
            .check_input(ByteCount::new(final_pages * PAGE_BYTES as u64))?;
        for change in self.changes {
            combined.advance(change, file, budget)?;
        }
        for image in self.append {
            let page = combined.append(image, budget)?;
            let image = combined
                .append
                .last()
                .ok_or(WriteError::Mismatch("missing sequential append"))?;
            write(file, page, image, budget)?;
        }
        Ok(())
    }

    fn advance(
        &mut self,
        change: Change,
        file: &mut File,
        budget: &mut ResourceBudget,
    ) -> Result<(), WriteError> {
        if change.page.get() >= self.first_append {
            let position = usize::try_from(change.page.get() - self.first_append)
                .map_err(|_| WriteError::Mismatch("sequential append ordinal"))?;
            let previous = self
                .append
                .get_mut(position)
                .ok_or(WriteError::Mismatch("sequential appended page absent"))?;
            budget.charge_work_units(PAGE_BYTES as u64)?;
            if previous.as_bytes() != &change.before {
                return Err(WriteError::Mismatch("sequential appended page changed"));
            }
            write(file, change.page, &change.after, budget)?;
            *previous = change.after;
        } else {
            budget.charge_work_units(self.changes.len() as u64 + PAGE_BYTES as u64)?;
            if let Some(previous) = self.changes.iter_mut().find(|c| c.page == change.page) {
                if previous.after.as_bytes() != &change.before {
                    return Err(WriteError::Mismatch("sequential source page changed"));
                }
                write(file, change.page, &change.after, budget)?;
                previous.after = change.after;
            } else {
                reserve(&mut self.changes, 1, budget)?;
                write(file, change.page, &change.after, budget)?;
                self.changes.push(change);
            }
        }
        Ok(())
    }

    pub(crate) fn verify_private(
        &self,
        original: &mut FileSource,
        candidate: &mut FileSource,
        budget: &mut ResourceBudget,
    ) -> Result<(), WriteError> {
        let mut changes = Vec::new();
        reserve(&mut changes, self.changes.len(), budget)?;
        changes.extend(self.changes.iter().map(|change| PageChange {
            page: change.page,
            before: &change.before,
            after: change.after.as_bytes(),
        }));
        crate::write::update_pages::verify_changes(
            original,
            candidate,
            &changes,
            &self.append,
            self.next_append_page()?.get() * PAGE_BYTES as u64,
            budget,
        )
    }
}

fn write(
    file: &mut File,
    page: PageNumber,
    image: &PageImage,
    budget: &mut ResourceBudget,
) -> Result<(), WriteError> {
    budget.charge_encoded_bytes(ByteCount::new(PAGE_BYTES as u64))?;
    budget.charge_work_units(PAGE_BYTES as u64)?;
    file.seek(SeekFrom::Start(page.get() * PAGE_BYTES as u64))?;
    file.write_all(image.as_bytes())?;
    Ok(())
}
