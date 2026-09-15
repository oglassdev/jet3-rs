//! Allocation-map edits and deferred bitmap placement (SRC-0020, EXP-0051/0057/0254).
use super::mutation_map::{BitSpan, MapBits};
use crate::allocation::{AllocationMapLayout, EXTENDED_BITMAP_BITS};
use crate::page_edits::{PageEdits, reserve};
use crate::{
    DatabaseReader, FileSource, PageImage, PageKind, PageNumber, ResourceBudget, UpdateError,
};

pub(crate) struct PendingMap {
    pub bits: MapBits,
    changes: Vec<(PageNumber, bool)>,
}

impl PendingMap {
    pub fn new(bits: MapBits) -> Self {
        Self {
            bits,
            changes: Vec::new(),
        }
    }

    pub fn change(
        &mut self,
        page: PageNumber,
        expected: bool,
        desired: bool,
        eof: u64,
        budget: &mut ResourceBudget,
    ) -> Result<(), UpdateError> {
        budget.charge_work_units(self.changes.len() as u64 + 1)?;
        let global = self.is_global();
        let prior = self.changes.iter_mut().find(|(member, _)| *member == page);
        let old = match &prior {
            Some((_, value)) => *value,
            None if global && page.get() >= eof && !self.bits.represents(page) => true,
            None => self.bits.contains(page)?,
        };
        if old != expected {
            return Err(UpdateError::Mismatch("allocation map membership"));
        }
        if let Some((_, value)) = prior {
            *value = desired;
        } else {
            reserve(&mut self.changes, 1, budget)?;
            self.changes.push((page, desired));
        }
        Ok(())
    }

    pub fn is_global(&self) -> bool {
        self.bits.locator == global_locator()
    }

    pub fn apply(
        mut self,
        database: &mut DatabaseReader<FileSource>,
        edits: &mut PageEdits,
        budget: &mut ResourceBudget,
    ) -> Result<(), UpdateError> {
        let global = self.is_global();
        let mut original = Vec::new();
        reserve(&mut original, self.bits.row.len(), budget)?;
        original.extend_from_slice(&self.bits.row);
        for (page, desired) in std::mem::take(&mut self.changes) {
            self.bits.set(
                page,
                desired,
                global,
                database.geometry().page_count(),
                edits,
                budget,
            )?;
        }
        if global {
            // Include every newly placed bitmap in its own global allocation state.
            let mut number = database.geometry().page_count();
            while number < edits.next_append_page()?.get() {
                self.bits.set(
                    PageNumber::new(number),
                    false,
                    true,
                    database.geometry().page_count(),
                    edits,
                    budget,
                )?;
                number += 1;
            }
        }
        self.bits.stage(database, edits, &original, budget)
    }
}

pub(crate) fn global_locator() -> crate::MapRowLocator {
    crate::MapRowLocator::new(PageNumber::new(1), 0)
}

impl MapBits {
    fn inline_bit(&self, page: PageNumber) -> Option<usize> {
        let AllocationMapLayout::Inline { start_page, bitmap } = &self.layout else {
            return None;
        };
        page.get()
            .checked_sub(start_page.get())
            .filter(|bit| bit / 8 < bitmap.len() as u64)
            .map(|bit| bit as usize)
    }

    fn set(
        &mut self,
        page: PageNumber,
        desired: bool,
        global: bool,
        eof: u64,
        edits: &mut PageEdits,
        budget: &mut ResourceBudget,
    ) -> Result<(), UpdateError> {
        budget.charge_work_units(1)?;
        if let Some(bit) = self.inline_bit(page) {
            let span = self
                .spans
                .first_mut()
                .ok_or(UpdateError::Mismatch("inline bitmap span"))?;
            put_bit(&mut span.bytes, bit, desired)?;
            return Ok(());
        }
        if !desired && !global {
            // A missing window already has no membership.
            if matches!(self.layout, AllocationMapLayout::Inline { .. }) {
                return Ok(());
            }
            let slot = page.get() / EXTENDED_BITMAP_BITS;
            if !self
                .spans
                .iter()
                .any(|span| span.first / EXTENDED_BITMAP_BITS == slot)
            {
                return Ok(());
            }
        }
        if matches!(self.layout, AllocationMapLayout::Inline { .. }) {
            self.convert(global, eof, edits, budget)?;
        }
        let position = self.ensure_slot(
            page.get() / EXTENDED_BITMAP_BITS,
            global,
            eof,
            edits,
            budget,
        )?;
        put_bit(
            &mut self.spans[position].bytes,
            (page.get() % EXTENDED_BITMAP_BITS) as usize,
            desired,
        )
    }

    fn convert(
        &mut self,
        global: bool,
        eof: u64,
        edits: &mut PageEdits,
        budget: &mut ResourceBudget,
    ) -> Result<(), UpdateError> {
        let length = crate::usage_map_writer::INDIRECT_ROW_BYTES;
        if self.row.len() < length {
            let additional = length - self.row.len();
            reserve(&mut self.row, additional, budget)?;
        }
        self.row.resize(length, 0);
        let old = std::mem::take(&mut self.spans);
        self.row.fill(0);
        self.row[0] = 1;
        self.layout = AllocationMapLayout::Indirect {
            references: 1..self.row.len(),
        };
        for span in old {
            budget.charge_items(span.bytes.len() as u64 * 8)?;
            for (byte, value) in span.bytes.into_iter().enumerate() {
                for bit in 0..8 {
                    if value & (1 << bit) == 0 {
                        continue;
                    }
                    let page = span
                        .first
                        .checked_add(byte as u64 * 8 + bit)
                        .ok_or(UpdateError::Mismatch("converted allocation bit"))?;
                    let position =
                        self.ensure_slot(page / EXTENDED_BITMAP_BITS, global, eof, edits, budget)?;
                    put_bit(
                        &mut self.spans[position].bytes,
                        (page % EXTENDED_BITMAP_BITS) as usize,
                        true,
                    )?;
                }
            }
        }
        Ok(())
    }

    fn ensure_slot(
        &mut self,
        slot: u64,
        global: bool,
        eof: u64,
        edits: &mut PageEdits,
        budget: &mut ResourceBudget,
    ) -> Result<usize, UpdateError> {
        if slot >= crate::usage_map_writer::INDIRECT_REFERENCE_SLOTS as u64 {
            return Err(UpdateError::Unsupported(
                "indirect allocation reference capacity",
            ));
        }
        // EXP-0057: active references form a prefix before the zero slots.
        while self.spans.len() <= slot as usize {
            let ordinal = self.spans.len();
            let first = ordinal as u64 * EXTENDED_BITMAP_BITS;
            let mut bytes = Vec::new();
            reserve(&mut bytes, (EXTENDED_BITMAP_BITS / 8) as usize, budget)?;
            bytes.resize((EXTENDED_BITMAP_BITS / 8) as usize, 0);
            if global {
                let start = eof.saturating_sub(first).min(EXTENDED_BITMAP_BITS);
                budget.charge_work_units(EXTENDED_BITMAP_BITS - start)?;
                for bit in start..EXTENDED_BITMAP_BITS {
                    put_bit(&mut bytes, bit as usize, true)?;
                }
            }
            reserve(&mut self.spans, 1, budget)?;
            let page = edits.append(PageImage::new(PageKind::ExtendedUsageBitmap), budget)?;
            let offset = 1 + ordinal * 4;
            let reference = u32::try_from(page.get())
                .map_err(|_| UpdateError::Mismatch("bitmap reference width"))?;
            self.row[offset..offset + 4].copy_from_slice(&reference.to_le_bytes());
            self.spans.push(BitSpan {
                first,
                page,
                offset: 4,
                bytes,
            });
        }
        Ok(slot as usize)
    }

    fn stage(
        mut self,
        database: &mut DatabaseReader<FileSource>,
        edits: &mut PageEdits,
        original: &[u8],
        budget: &mut ResourceBudget,
    ) -> Result<(), UpdateError> {
        match self.layout {
            AllocationMapLayout::Inline { bitmap, .. } => {
                let span = self
                    .spans
                    .first()
                    .ok_or(UpdateError::Mismatch("inline map storage"))?;
                self.row[bitmap].copy_from_slice(&span.bytes);
                edits.map_record(database, self.locator, original, &self.row, budget)?;
            }
            AllocationMapLayout::Indirect { .. } => {
                edits.map_record(database, self.locator, original, &self.row, budget)?;
                for span in self.spans {
                    edits.patch_bytes(database, span.page, 0, &[5, 1, 0, 0], budget)?;
                    edits.patch_bytes(database, span.page, span.offset, &span.bytes, budget)?;
                }
            }
        }
        Ok(())
    }
}

fn put_bit(bytes: &mut [u8], bit: usize, desired: bool) -> Result<(), UpdateError> {
    let target = bytes
        .get_mut(bit / 8)
        .ok_or(UpdateError::Mismatch("allocation bit offset"))?;
    let mask = 1 << (bit % 8);
    if desired {
        *target |= mask;
    } else {
        *target &= !mask;
    }
    Ok(())
}

#[cfg(test)]
#[path = "mutation_map_write_tests.rs"]
mod tests;
