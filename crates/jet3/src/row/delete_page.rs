//! Slot-preserving compaction and tombstones from EXP-0162 (EXP-0059/0060 layout).
use crate::{
    PAGE_BYTES, PageImage, PageOffset, ResourceBudget, WriteError,
    row::{
        data_page::{DataPageEditor, Packed, write_free, write_slot},
        directory::RowSlot,
    },
};

const TOMBSTONE: u16 = 0xc000;
// EXP-0162/0224 last-row deletion changes tag, free bytes and directory words.
const RELEASED_PAGE_TAG: u8 = 0x09;

pub(crate) enum Deletion {
    Retained(PageImage),
    Released(PageImage),
}

impl Deletion {
    pub(crate) fn into_image(self) -> PageImage {
        match self {
            Self::Retained(image) | Self::Released(image) => image,
        }
    }

    #[cfg(test)]
    pub fn image(&self) -> &PageImage {
        match self {
            Self::Retained(image) | Self::Released(image) => image,
        }
    }
}

impl DataPageEditor<'_> {
    /// Slot-preserving compaction and tombstones from EXP-0162.
    pub(crate) fn remove(
        &self,
        slot: u8,
        physical: bool,
        budget: &mut ResourceBudget,
    ) -> Result<Deletion, WriteError> {
        let count = self.directory.row_count();
        let range = self.entry(slot)?.range();
        budget.charge_work_units(2 * u64::from(count))?;
        let live = self.live_rows(physical, "page contains an unsupported row slot")?;
        if range.is_empty() {
            return Err(WriteError::NotFound("live row slot"));
        }
        let Packed { lowest, free, .. } = self.packed("data page free-byte count")?;
        let removed = range.len();
        let new_free =
            u16::try_from(free + removed).map_err(|_| WriteError::Mismatch("free-byte range"))?;
        let mut patched = PageImage::from_bytes(*self.source);
        if live == 1 {
            patched.write_at(PageOffset::new(0), &[RELEASED_PAGE_TAG], budget)?;
            for ordinal in 0..count {
                write_slot(&mut patched, ordinal, TOMBSTONE | PAGE_BYTES as u16, budget)?;
            }
            write_free(&mut patched, new_free, budget)?;
            return Ok(Deletion::Released(patched));
        }
        // EXP-0162 moves later row bytes upward and leaves the vacated slack intact.
        budget.charge_work_units((range.start - lowest) as u64)?;
        patched.write_at(
            PageOffset::new((lowest + removed) as u64),
            &self.source[lowest..range.start],
            budget,
        )?;
        for ordinal in u16::from(slot)..count {
            let entry = self.entry(ordinal as u8)?;
            let start = if ordinal == u16::from(slot) {
                range.end
            } else {
                entry
                    .range()
                    .start
                    .checked_add(removed)
                    .filter(|v| *v <= PAGE_BYTES)
                    .ok_or(WriteError::Mismatch("compacted row offset"))?
            };
            let flags = if ordinal == u16::from(slot) {
                TOMBSTONE
            } else {
                RowSlot::read(&entry)?.flags()
            };
            let word =
                u16::try_from(start).map_err(|_| WriteError::Mismatch("tombstone offset"))? | flags;
            write_slot(&mut patched, ordinal, word, budget)?;
        }
        write_free(&mut patched, new_free, budget)?;
        Ok(Deletion::Retained(patched))
    }
}
