//! Row edits on one checked data page in the EXP-0059/0060 directory layout.
use crate::{
    PAGE_BYTES, PageImage, PageNumber, PageOffset, ResourceBudget, UpdateError,
    definition::header::ROW_COUNT as TABLE_ROW_COUNT,
    format::{
        data_page_directory::{DIRECTORY_OFFSET, ENTRY_LEN, FREE_SPACE_OFFSET, ROW_COUNT_OFFSET},
        page_image::MAX_BUILT_ROWS,
    },
    row::directory::{RowDirectory, RowEntry, RowSlot},
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

/// A data page whose directory was validated for `owner`; edits return patched copies.
///
/// Ordinary edits (`physical == false`) refuse pages holding hidden or overflow
/// rows other than deleted slots; physical edits accept every checked [`RowSlot`].
pub(crate) struct DataPageEditor<'a> {
    source: &'a [u8; PAGE_BYTES],
    directory: RowDirectory,
}

struct Packed {
    lowest: usize,
    directory_end: usize,
    free: usize,
}

impl<'a> DataPageEditor<'a> {
    pub(crate) fn open(
        page: PageNumber,
        owner: PageNumber,
        source: &'a [u8; PAGE_BYTES],
        budget: &mut ResourceBudget,
    ) -> Result<Self, UpdateError> {
        let directory = RowDirectory::validate(page, owner, source, budget)?;
        Ok(Self { source, directory })
    }

    fn entry(&self, slot: u8) -> Result<RowEntry, UpdateError> {
        Ok(self.directory.entry(self.source, slot)?)
    }

    fn live_rows(&self, physical: bool, unsupported: &'static str) -> Result<u16, UpdateError> {
        let mut live = 0;
        for ordinal in 0..self.directory.row_count() {
            let entry = self.entry(ordinal as u8)?;
            if physical {
                if RowSlot::read(&entry)? != RowSlot::Deleted {
                    live += 1;
                }
                continue;
            }
            if entry.hidden() && entry.overflow() && entry.range().is_empty() {
                continue;
            }
            if entry.hidden() || entry.overflow() || entry.range().is_empty() {
                return Err(UpdateError::Unsupported(unsupported));
            }
            live += 1;
        }
        Ok(live)
    }

    /// Requires the stored free-byte count to equal the gap below the lowest row.
    fn packed(&self, mismatch: &'static str) -> Result<Packed, UpdateError> {
        let count = self.directory.row_count();
        let last = count
            .checked_sub(1)
            .ok_or(UpdateError::Mismatch(mismatch))?;
        let lowest = self.entry(last as u8)?.range().start;
        let directory_end = DIRECTORY_OFFSET + ENTRY_LEN * usize::from(count);
        let free = usize::from(u16::from_le_bytes([
            self.source[FREE_SPACE_OFFSET],
            self.source[FREE_SPACE_OFFSET + 1],
        ]));
        if free != lowest - directory_end {
            return Err(UpdateError::Mismatch(mismatch));
        }
        Ok(Packed {
            lowest,
            directory_end,
            free,
        })
    }

    /// EXP-0162 append within the EXP-0305 slot limit; `None` when the row does not fit.
    pub(crate) fn append(
        &self,
        row: &[u8],
        physical: Option<RowSlot>,
        budget: &mut ResourceBudget,
    ) -> Result<Option<(PageImage, u8)>, UpdateError> {
        let count = self.directory.row_count();
        if count == 0 {
            return Ok(None);
        }
        budget.charge_items(u64::from(count))?;
        if self.live_rows(physical.is_some(), "page contains nonordinary row slots")? == 0 {
            return Ok(None);
        }
        let packed = self.packed("data page free-byte count")?;
        let state = physical.unwrap_or(RowSlot::Ordinary);
        state.check_length(row.len())?;
        if state == RowSlot::Deleted {
            return Err(UpdateError::Mismatch("appending a deleted row"));
        }
        let needed = row
            .len()
            .checked_add(ENTRY_LEN)
            .ok_or(UpdateError::Mismatch("row width"))?;
        if count >= MAX_BUILT_ROWS || packed.free < needed {
            return Ok(None);
        }
        let start = packed
            .lowest
            .checked_sub(row.len())
            .ok_or(UpdateError::Mismatch("row start"))?;
        let new_free =
            u16::try_from(packed.free - needed).map_err(|_| UpdateError::Mismatch("free bytes"))?;
        let word =
            u16::try_from(start).map_err(|_| UpdateError::Mismatch("row offset"))? | state.flags();
        let mut patched = PageImage::from_bytes(*self.source);
        patched.write_at(PageOffset::new(start as u64), row, budget)?;
        write_slot(&mut patched, count, word, budget)?;
        write_free(&mut patched, new_free, budget)?;
        patched.write_at(
            PageOffset::new(ROW_COUNT_OFFSET as u64),
            &(count + 1).to_le_bytes(),
            budget,
        )?;
        Ok(Some((patched, count as u8)))
    }

    /// Same-page replacement (EXP-0060/0061 encoding, EXP-0162 movement); `None` when
    /// the page lacks room.
    pub(crate) fn replace(
        &self,
        slot: u8,
        encoded: &[u8],
        physical: Option<RowSlot>,
        budget: &mut ResourceBudget,
    ) -> Result<Option<PageImage>, UpdateError> {
        let count = self.directory.row_count();
        let target = self.entry(slot)?;
        let previous = RowSlot::read(&target)?;
        if previous == RowSlot::Deleted || (physical.is_none() && previous != RowSlot::Ordinary) {
            return Err(UpdateError::Unsupported(
                "replacement target is not an ordinary live row",
            ));
        }
        budget.charge_work_units(2 * u64::from(count))?;
        self.live_rows(
            physical.is_some(),
            "replacement page contains an unsupported row slot",
        )?;
        let old = target.range();
        let Packed {
            lowest,
            directory_end,
            free,
        } = self.packed("replacement free-byte count")?;
        let Some(new_free) = free
            .checked_add(old.len())
            .and_then(|space| space.checked_sub(encoded.len()))
        else {
            return Ok(None);
        };
        let state = physical.unwrap_or(RowSlot::Ordinary);
        state.check_length(encoded.len())?;
        if encoded.is_empty() {
            return Err(UpdateError::Unsupported("empty replacement row"));
        }
        let new_start = old
            .end
            .checked_sub(encoded.len())
            .ok_or(UpdateError::Mismatch("replacement row boundary"))?;
        let mut image = PageImage::from_bytes(*self.source);
        budget.charge_work_units((old.start - lowest) as u64)?;
        image.write_at(
            PageOffset::new((directory_end + new_free) as u64),
            &self.source[lowest..old.start],
            budget,
        )?;
        image.write_at(PageOffset::new(new_start as u64), encoded, budget)?;
        for ordinal in u16::from(slot)..count {
            let entry = self.entry(ordinal as u8)?;
            let offset = entry
                .range()
                .start
                .checked_add(old.len())
                .and_then(|v| v.checked_sub(encoded.len()))
                .filter(|v| *v <= PAGE_BYTES)
                .ok_or(UpdateError::Mismatch("replacement slot offset"))?;
            let flags = if ordinal == u16::from(slot) {
                state.flags()
            } else {
                RowSlot::read(&entry)?.flags()
            };
            let word = u16::try_from(offset)
                .map_err(|_| UpdateError::Mismatch("replacement slot width"))?
                | flags;
            write_slot(&mut image, ordinal, word, budget)?;
        }
        let free = u16::try_from(new_free)
            .map_err(|_| UpdateError::Mismatch("replacement free-byte width"))?;
        write_free(&mut image, free, budget)?;
        Ok(Some(image))
    }

    /// Slot-preserving compaction and tombstones from EXP-0162.
    pub(crate) fn remove(
        &self,
        slot: u8,
        physical: bool,
        budget: &mut ResourceBudget,
    ) -> Result<Deletion, UpdateError> {
        let count = self.directory.row_count();
        let range = self.entry(slot)?.range();
        budget.charge_work_units(2 * u64::from(count))?;
        let live = self.live_rows(physical, "page contains an unsupported row slot")?;
        if range.is_empty() {
            return Err(UpdateError::NotFound("live row slot"));
        }
        let Packed { lowest, free, .. } = self.packed("data page free-byte count")?;
        let removed = range.len();
        let new_free =
            u16::try_from(free + removed).map_err(|_| UpdateError::Mismatch("free-byte range"))?;
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
                    .ok_or(UpdateError::Mismatch("compacted row offset"))?
            };
            let flags = if ordinal == u16::from(slot) {
                TOMBSTONE
            } else {
                RowSlot::read(&entry)?.flags()
            };
            let word = u16::try_from(start)
                .map_err(|_| UpdateError::Mismatch("tombstone offset"))?
                | flags;
            write_slot(&mut patched, ordinal, word, budget)?;
        }
        write_free(&mut patched, new_free, budget)?;
        Ok(Deletion::Retained(patched))
    }
}

fn write_slot(
    image: &mut PageImage,
    ordinal: u16,
    word: u16,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    let offset = DIRECTORY_OFFSET + ENTRY_LEN * usize::from(ordinal);
    image.write_at(PageOffset::new(offset as u64), &word.to_le_bytes(), budget)?;
    Ok(())
}

fn write_free(
    image: &mut PageImage,
    free: u16,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    image.write_at(
        PageOffset::new(FREE_SPACE_OFFSET as u64),
        &free.to_le_bytes(),
        budget,
    )?;
    Ok(())
}

/// Candidate availability policy: an appended minimum row and directory slot fit.
/// EXP-0060 supplies the physical slots; this does not model DAO's allocation policy.
pub(crate) fn has_capacity(page: &[u8; PAGE_BYTES], minimum: usize) -> bool {
    let count = u16::from_le_bytes([page[ROW_COUNT_OFFSET], page[ROW_COUNT_OFFSET + 1]]);
    let free = usize::from(u16::from_le_bytes([
        page[FREE_SPACE_OFFSET],
        page[FREE_SPACE_OFFSET + 1],
    ]));
    count < MAX_BUILT_ROWS && minimum.checked_add(ENTRY_LEN).is_some_and(|n| free >= n)
}

/// Requires the table definition's stored row count to equal the rows read.
pub(crate) fn check_table_rows(
    source: &[u8; PAGE_BYTES],
    observed: u32,
) -> Result<(), UpdateError> {
    if source[TABLE_ROW_COUNT..TABLE_ROW_COUNT + 4] != observed.to_le_bytes() {
        return Err(UpdateError::Mismatch("table row count"));
    }
    Ok(())
}

/// Checks the stored row count, then writes it one higher (`inserted`) or lower.
pub(crate) fn count_table_row(
    source: &[u8; PAGE_BYTES],
    observed: u32,
    inserted: bool,
    budget: &mut ResourceBudget,
) -> Result<PageImage, UpdateError> {
    check_table_rows(source, observed)?;
    let count = if inserted {
        observed
            .checked_add(1)
            .ok_or(UpdateError::Mismatch("table row count overflow"))?
    } else {
        observed
            .checked_sub(1)
            .ok_or(UpdateError::Mismatch("empty table"))?
    };
    let mut patched = PageImage::from_bytes(*source);
    patched.write_at(
        PageOffset::new(TABLE_ROW_COUNT as u64),
        &count.to_le_bytes(),
        budget,
    )?;
    Ok(patched)
}
