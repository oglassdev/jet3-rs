//! Same-page row replacement from EXP-0060/0061 encoding and EXP-0162 movement.
use crate::{
    PAGE_BYTES, PageImage, PageOffset, ResourceBudget, WriteError,
    row::{
        data_page::{DataPageEditor, Packed, write_free, write_slot},
        directory::RowSlot,
    },
};

impl DataPageEditor<'_> {
    /// Same-page replacement (EXP-0060/0061 encoding, EXP-0162 movement); `None` when
    /// the page lacks room.
    pub(crate) fn replace(
        &self,
        slot: u8,
        encoded: &[u8],
        physical: Option<RowSlot>,
        budget: &mut ResourceBudget,
    ) -> Result<Option<PageImage>, WriteError> {
        let count = self.directory.row_count();
        let target = self.entry(slot)?;
        let previous = RowSlot::read(&target)?;
        if previous == RowSlot::Deleted || (physical.is_none() && previous != RowSlot::Ordinary) {
            return Err(WriteError::Unsupported(
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
            return Err(WriteError::Unsupported("empty replacement row"));
        }
        let new_start = old
            .end
            .checked_sub(encoded.len())
            .ok_or(WriteError::Mismatch("replacement row boundary"))?;
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
                .ok_or(WriteError::Mismatch("replacement slot offset"))?;
            let flags = if ordinal == u16::from(slot) {
                state.flags()
            } else {
                RowSlot::read(&entry)?.flags()
            };
            let word = u16::try_from(offset)
                .map_err(|_| WriteError::Mismatch("replacement slot width"))?
                | flags;
            write_slot(&mut image, ordinal, word, budget)?;
        }
        let free = u16::try_from(new_free)
            .map_err(|_| WriteError::Mismatch("replacement free-byte width"))?;
        write_free(&mut image, free, budget)?;
        Ok(Some(image))
    }
}
