//! Detached mutable-map inputs from SRC-0020 and EXP-0051/0057/0254.
use crate::allocation::{AllocationMapLayout, EXTENDED_BITMAP_BITS, decode_allocation_map_layout};
use crate::page_edits::reserve;
use crate::{
    DatabaseReader, MapRowLocator, PAGE_BYTES, PageKind, PageNumber, ReadAt, ResourceBudget,
    UpdateError,
};
use std::ops::Range;

pub(crate) struct MapBits {
    pub locator: MapRowLocator,
    pub range: Range<usize>,
    pub row: Vec<u8>,
    pub layout: AllocationMapLayout,
    pub spans: Vec<BitSpan>,
}

pub(crate) struct BitSpan {
    pub first: u64,
    pub page: PageNumber,
    pub offset: usize,
    pub bytes: Vec<u8>,
}

impl MapBits {
    pub fn load<S: ReadAt>(
        database: &mut DatabaseReader<S>,
        locator: MapRowLocator,
        budget: &mut ResourceBudget,
    ) -> Result<Self, UpdateError> {
        let mut bytes = [0; PAGE_BYTES];
        let page = database
            .read_classified_page(locator.page(), &mut bytes, budget)
            .map_err(crate::TableDefinitionError::Page)?;
        let record =
            crate::locate_usage_map(page, locator, budget).map_err(UpdateError::UsageMap)?;
        let layout =
            decode_allocation_map_layout(record.raw(), budget).map_err(UpdateError::Allocation)?;
        if locator == crate::mutation_map_write::global_locator()
            && matches!(layout, AllocationMapLayout::Inline { start_page, .. } if start_page.get() != 0)
        {
            return Err(UpdateError::Mismatch("global inline map base"));
        }
        let mut row = Vec::new();
        reserve(&mut row, record.raw().len(), budget)?;
        row.extend_from_slice(record.raw());
        let mut result = Self {
            locator,
            range: record.range(),
            row,
            layout,
            spans: Vec::new(),
        };
        match result.layout.clone() {
            AllocationMapLayout::Inline { start_page, bitmap } => {
                result.push_span(
                    start_page.get(),
                    locator.page(),
                    result.range.start + bitmap.start,
                    &record.raw()[bitmap],
                    budget,
                )?;
            }
            AllocationMapLayout::Indirect { references } => {
                if result.row.len() != crate::usage_map_writer::INDIRECT_ROW_BYTES {
                    return Err(UpdateError::Unsupported(
                        "indirect allocation map row width",
                    ));
                }
                let mut zero_seen = false;
                for (slot, raw) in record.raw()[references].chunks_exact(4).enumerate() {
                    budget.charge_items(1)?;
                    let reference = u32::from_le_bytes([raw[0], raw[1], raw[2], raw[3]]);
                    if reference == 0 {
                        zero_seen = true;
                        continue;
                    }
                    if zero_seen {
                        return Err(UpdateError::Mismatch(
                            "nonzero bitmap reference after empty slot",
                        ));
                    }
                    let number = PageNumber::new(u64::from(reference));
                    budget.charge_work_units(result.spans.len() as u64)?;
                    if number == locator.page()
                        || result.spans.iter().any(|span| span.page == number)
                    {
                        return Err(UpdateError::Mismatch("aliased indirect bitmap page"));
                    }
                    let mut extended = [0; PAGE_BYTES];
                    let page = database
                        .read_classified_page(number, &mut extended, budget)
                        .map_err(crate::TableDefinitionError::Page)?;
                    if page.kind() != PageKind::ExtendedUsageBitmap || extended[..4] != [5, 1, 0, 0]
                    {
                        return Err(UpdateError::Mismatch("indirect bitmap page header"));
                    }
                    let first = (slot as u64)
                        .checked_mul(EXTENDED_BITMAP_BITS)
                        .ok_or(UpdateError::Mismatch("indirect bitmap slot"))?;
                    result.push_span(first, number, 4, &extended[4..], budget)?;
                }
            }
        }
        Ok(result)
    }

    fn push_span(
        &mut self,
        first: u64,
        page: PageNumber,
        offset: usize,
        input: &[u8],
        budget: &mut ResourceBudget,
    ) -> Result<(), UpdateError> {
        reserve(&mut self.spans, 1, budget)?;
        let mut bytes = Vec::new();
        reserve(&mut bytes, input.len(), budget)?;
        budget.charge_work_units(input.len() as u64)?;
        bytes.extend_from_slice(input);
        self.spans.push(BitSpan {
            first,
            page,
            offset,
            bytes,
        });
        Ok(())
    }

    pub fn represents(&self, page: PageNumber) -> bool {
        let position = self.spans.partition_point(|span| span.first <= page.get());
        position
            .checked_sub(1)
            .and_then(|n| self.spans.get(n))
            .is_some_and(|span| (page.get() - span.first) / 8 < span.bytes.len() as u64)
    }

    /// A missing indirect slot or a page outside an inline window has no set bit.
    pub fn contains(&self, page: PageNumber) -> Result<bool, UpdateError> {
        let position = self.spans.partition_point(|span| span.first <= page.get());
        let Some(span) = position.checked_sub(1).and_then(|n| self.spans.get(n)) else {
            return Ok(false);
        };
        let bit = page.get() - span.first;
        if bit / 8 >= span.bytes.len() as u64 {
            return Ok(false);
        }
        Ok(span.bytes[(bit / 8) as usize] & (1 << (bit % 8)) != 0)
    }

    pub fn overlaps(&self, other: &Self, budget: &mut ResourceBudget) -> Result<bool, UpdateError> {
        budget.charge_work_units(
            (self.spans.len() as u64 + 1).saturating_mul(other.spans.len() as u64 + 1),
        )?;
        if self.locator.page() == other.locator.page()
            && self.range.start < other.range.end
            && other.range.start < self.range.end
        {
            return Ok(true);
        }
        for span in &self.spans {
            for other in &other.spans {
                if span.page == other.page
                    && span.offset < other.offset + other.bytes.len()
                    && other.offset < span.offset + span.bytes.len()
                {
                    return Ok(true);
                }
            }
        }
        Ok(false)
    }

    pub fn existing_pages(
        &self,
        page_count: u64,
        allow_future: bool,
        budget: &mut ResourceBudget,
    ) -> Result<Vec<PageNumber>, UpdateError> {
        let mut pages = Vec::new();
        for span in &self.spans {
            budget.charge_items(span.bytes.len() as u64 * 8)?;
            for (byte, value) in span.bytes.iter().copied().enumerate() {
                for bit in 0..8 {
                    if value & (1 << bit) == 0 {
                        continue;
                    }
                    let page = span
                        .first
                        .checked_add(byte as u64 * 8 + bit)
                        .ok_or(UpdateError::Mismatch("allocation map page number"))?;
                    if page >= page_count {
                        if allow_future {
                            continue;
                        }
                        return Err(UpdateError::Mismatch("owned page outside file"));
                    }
                    reserve(&mut pages, 1, budget)?;
                    pages.push(PageNumber::new(page));
                }
            }
        }
        Ok(pages)
    }
}
