//! Fixed-size map rows with indirect storage from SRC-0020 and EXP-0057/0254.
use super::*;
use crate::{ExtendedUsageMapEncoder, encode_indirect_references};

// A 133-byte row contains 33 independent reference slots (EXP-0254).
const REFERENCE_COUNT: usize = crate::usage_map_writer::INDIRECT_REFERENCE_SLOTS;
pub(super) const PAGE_LIMIT: u64 = REFERENCE_COUNT as u64 * crate::EXTENDED_BITMAP_BITS;

pub(super) struct AllocationMaps {
    first_extra: Option<u64>,
    extra: Vec<ExtendedUsageMapEncoder>,
}

impl AllocationMaps {
    pub fn new(first_extra: u64) -> Self {
        Self {
            first_extra: Some(first_extra),
            extra: Vec::new(),
        }
    }
    pub fn inline_only() -> Self {
        Self {
            first_extra: None,
            extra: Vec::new(),
        }
    }

    pub fn row<I>(
        &mut self,
        pages: I,
        budget: &mut ResourceBudget,
    ) -> Result<[u8; 133], ComposeError>
    where
        I: Iterator<Item = u64> + Clone,
    {
        let mut highest = 0;
        for page in pages.clone() {
            budget.charge_items(1)?;
            highest = highest.max(page);
        }
        if highest < MAP_BITMAP_BYTES * 8 {
            let mut map = InlineUsageMapEncoder::new(
                PageNumber::new(0),
                ByteCount::new(MAP_BITMAP_BYTES),
                budget,
            )?;
            for page in pages {
                map.set_page(PageNumber::new(page))?;
            }
            let mut row = [0; 133];
            map.encode_into(&mut row, budget)?;
            return Ok(row);
        }
        check_page(highest)?;
        let mut refs = [PageNumber::new(0); REFERENCE_COUNT];
        let mut positions = [usize::MAX; REFERENCE_COUNT];
        for page in pages {
            budget.charge_items(1)?;
            let slot = (page / crate::EXTENDED_BITMAP_BITS) as usize;
            for previous in 0..=slot {
                if positions[previous] == usize::MAX {
                    let (position, number) = self.allocate(previous as u64, budget)?;
                    positions[previous] = position;
                    refs[previous] = number;
                }
            }
            self.extra[positions[slot]].set_page(PageNumber::new(page), budget)?;
        }
        let mut row = [0; 133];
        encode_indirect_references(&refs, &mut row, budget)?;
        Ok(row)
    }

    fn allocate(
        &mut self,
        slot: u64,
        budget: &mut ResourceBudget,
    ) -> Result<(usize, PageNumber), ComposeError> {
        let first = self.first_extra.ok_or(ComposeError::CatalogPageLimit {
            maximum: MAP_BITMAP_BYTES * 8,
        })?;
        let number = first
            .checked_add(self.extra.len() as u64)
            .ok_or(Error::Arithmetic {
                operation: "place creation bitmap",
            })?;
        check_page(number)?;
        if self.extra.len() == self.extra.capacity() {
            let additional = self.extra.capacity().max(1);
            budget.charge_allocation(ByteCount::new(
                (additional * size_of::<ExtendedUsageMapEncoder>()) as u64,
            ))?;
            self.extra
                .try_reserve_exact(additional)
                .map_err(|_| Error::Io {
                    operation: "reserve creation bitmaps",
                    kind: std::io::ErrorKind::OutOfMemory,
                })?;
        }
        let position = self.extra.len();
        self.extra.push(ExtendedUsageMapEncoder::new(slot, budget)?);
        Ok((position, PageNumber::new(number)))
    }

    pub fn finish(
        mut self,
        plan: &mut WholeFileImagePlan,
        budget: &mut ResourceBudget,
    ) -> Result<(), ComposeError> {
        let first = self.first_extra.ok_or(ComposeError::CatalogLayout {
            detail: "missing allocation append base",
        })?;
        if first != plan.page_count() {
            return Err(ComposeError::CatalogLayout {
                detail: "allocation append order",
            });
        }
        let existing_end = first + self.extra.len() as u64;
        let global = if existing_end <= GLOBAL_BITMAP_BYTES * 8 {
            global_map_page(existing_end, budget)?
        } else {
            let mut count = existing_end.div_ceil(crate::EXTENDED_BITMAP_BITS);
            loop {
                let needed = (existing_end + count).div_ceil(crate::EXTENDED_BITMAP_BITS);
                if count == needed {
                    break;
                }
                count = needed;
            }
            let end = existing_end + count;
            check_page(end - 1)?;
            let mut references = [PageNumber::new(0); REFERENCE_COUNT];
            for slot in 0..count {
                let (position, number) = self.allocate(slot, budget)?;
                references[slot as usize] = number;
                let start = (slot * crate::EXTENDED_BITMAP_BITS).max(end);
                let stop = (slot + 1) * crate::EXTENDED_BITMAP_BITS;
                for page in start..stop {
                    self.extra[position].set_page(PageNumber::new(page), budget)?;
                }
            }
            let mut row = [0; 133];
            encode_indirect_references(&references, &mut row, budget)?;
            data_page(GLOBAL_MAP_PAGE, &[&row, &[0; 133]], budget)?
        };
        plan.replace(PageNumber::new(GLOBAL_MAP_PAGE), global)?;
        for map in self.extra {
            plan.append_image(map.into_image(), budget)?;
        }
        Ok(())
    }
}

pub(super) fn check_page(page: u64) -> Result<(), ComposeError> {
    if page >= PAGE_LIMIT {
        return Err(ComposeError::CatalogPageLimit {
            maximum: PAGE_LIMIT,
        });
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_late_first_member_keeps_an_active_reference_prefix() -> Result<(), ComposeError> {
        let mut maps = AllocationMaps::new(40000);
        let mut b = ResourceBudget::new(crate::ResourceLimits::default());
        let row = maps.row(std::iter::once(20000), &mut b)?;
        assert_eq!(&row[..9], &[1, 0x40, 0x9c, 0, 0, 0x41, 0x9c, 0, 0]);
        assert!(row[9..].iter().all(|byte| *byte == 0));
        assert_eq!(maps.extra.len(), 2);
        assert!(!maps.extra[0].is_set(PageNumber::new(0))?);
        assert!(!maps.extra[0].is_set(PageNumber::new(16351))?);
        assert!(maps.extra[1].is_set(PageNumber::new(20000))?);
        Ok(())
    }
}
