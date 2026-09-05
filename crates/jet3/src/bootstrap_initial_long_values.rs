//! Initial Memo/OLE construction from EXP-0061 headers/fragments, EXP-0077
//! column maps and EXP-0065 append placement. The inline cutoff (32 bytes),
//! one fragment per page, and allocation before data pages are candidate
//! policies, not inferred DAO allocation thresholds.

use super::*;
use crate::long_value_writer::{
    HEADER_LEN, MAX_CHAINED_FRAGMENT, MAX_SINGLE_PAGE_PAYLOAD, chained_fragments,
    encode_chained_row, encode_inline_long_value, external_long_value_header,
};
use crate::{ExternalLongValueStorage, RowLocator};

const INLINE_LIMIT: usize = 32;

#[derive(Debug, Clone)]
pub(super) struct InitialLongValues {
    first: u64,
    pages: Vec<(PageImage, bool)>,
}

pub(crate) fn initial_payload_start(
    table: &TableSpec<'_>,
    root: PageNumber,
    first_create: bool,
) -> Result<u64, ComposeError> {
    let plan = crate::table_schema_plan::plan_table_schema(table, root.get(), first_create)?;
    Ok(root.get() + plan.appended_page_count())
}

fn refusal(row: usize, detail: &'static str) -> ComposeError {
    ComposeError::InitialLongValue { row, detail }
}

fn external_shape(length: usize) -> (ExternalLongValueStorage, usize) {
    if length <= MAX_SINGLE_PAGE_PAYLOAD {
        (ExternalLongValueStorage::SinglePage, 1)
    } else {
        (
            ExternalLongValueStorage::Chained,
            length.div_ceil(MAX_CHAINED_FRAGMENT),
        )
    }
}

fn advance(next: &mut u64, count: usize) -> Result<(), ComposeError> {
    // SRC-0020/EXP-0057 inline coverage; no indirect growth policy is assumed.
    let limit = MAP_BITMAP_BYTES * 8;
    if *next >= limit || count as u64 > limit - *next {
        return Err(UsageMapWriteError::PageOutOfMap {
            page: PageNumber::new(limit),
            first: PageNumber::new(0),
            page_count: limit,
        }
        .into());
    }
    *next += count as u64;
    Ok(())
}

/// Lowers caller payloads to internally assigned headers before row encoding.
/// The running page number also makes publication's expected headers deterministic.
pub(crate) fn encode_initial_row(
    layout: &[RowColumnLayout],
    values: &[RowValue<'_>],
    row: usize,
    next: &mut u64,
    output: &mut [u8],
    budget: &mut ResourceBudget,
) -> Result<ByteCount, ComposeError> {
    if values.len() != layout.len() || values.len() > u8::MAX as usize {
        return encode_row(layout, values, output, budget).map_err(Into::into);
    }
    budget.charge_work_units(2 * values.len() as u64)?;
    let mut lowered = [RowValue::Null; u8::MAX as usize];
    lowered[..values.len()].copy_from_slice(values);
    let mut header = [0_u8; HEADER_LEN + INLINE_LIMIT];
    if values
        .iter()
        .any(|value| matches!(value, RowValue::LongValue(_)))
    {
        return Err(refusal(row, "caller-supplied long-value header"));
    }
    for (ordinal, value) in values.iter().enumerate() {
        budget.charge_items(1)?;
        let (payload, expected) = match value {
            RowValue::Memo(payload) => (*payload, ColumnPhysicalType::Memo),
            RowValue::LongBinary(payload) => (*payload, ColumnPhysicalType::LongBinary),
            _ => continue,
        };
        if layout[ordinal].physical_type() != expected {
            return Err(RowWriteError::TypeMismatch {
                ordinal: ordinal as u16,
                physical_type: layout[ordinal].physical_type(),
            }
            .into());
        }
        if payload.is_empty() {
            return Err(refusal(row, "empty payload"));
        }
        budget.check_decoded_value(ByteCount::new(payload.len() as u64))?;
        budget.charge_work_units((HEADER_LEN + payload.len().min(INLINE_LIMIT)) as u64)?;
        let length = if payload.len() <= INLINE_LIMIT {
            encode_inline_long_value(payload, &mut header)
                .map_err(|_| refusal(row, "inline payload encoding"))?
        } else {
            let (storage, count) = external_shape(payload.len());
            let target = RowLocator::new(PageNumber::new(*next), 0);
            let external = external_long_value_header(payload.len(), storage, target)
                .map_err(|_| refusal(row, "external payload encoding bounds"))?;
            advance(next, count)?;
            header[..HEADER_LEN].copy_from_slice(&external);
            HEADER_LEN
        };
        // The single-column schema restriction ensures this backing buffer is unique.
        lowered[ordinal] = RowValue::LongValue(&header[..length]);
        return encode_row(layout, &lowered[..values.len()], output, budget).map_err(Into::into);
    }
    encode_row(layout, values, output, budget).map_err(Into::into)
}

impl InitialLongValues {
    pub(super) fn new(
        first: u64,
        rows: &[&[RowValue<'_>]],
        budget: &mut ResourceBudget,
    ) -> Result<Self, ComposeError> {
        let mut result = Self {
            first,
            pages: Vec::new(),
        };
        for (row, values) in rows.iter().enumerate() {
            for value in *values {
                budget.charge_items(1)?;
                let payload = match value {
                    RowValue::Memo(payload) | RowValue::LongBinary(payload) => *payload,
                    _ => continue,
                };
                if payload.len() <= INLINE_LIMIT {
                    continue;
                }
                budget.check_decoded_value(ByteCount::new(payload.len() as u64))?;
                let (storage, count) = external_shape(payload.len());
                let start = result.first + result.pages.len() as u64;
                external_long_value_header(
                    payload.len(),
                    storage,
                    RowLocator::new(PageNumber::new(start), 0),
                )
                .map_err(|_| refusal(row, "external payload encoding bounds"))?;
                let mut end = start;
                advance(&mut end, count)?;
                let needed = result.pages.len() + count;
                if needed > result.pages.capacity() {
                    let capacity = needed
                        .max(result.pages.capacity() * 2)
                        .min((MAP_BITMAP_BYTES * 8) as usize);
                    budget.charge_allocation(ByteCount::new(
                        ((capacity - result.pages.capacity()) * size_of::<(PageImage, bool)>())
                            as u64,
                    ))?;
                    result
                        .pages
                        .try_reserve_exact(capacity - result.pages.len())
                        .map_err(|_| Error::Io {
                            operation: "reserve initial long-value pages",
                            kind: std::io::ErrorKind::OutOfMemory,
                        })?;
                }
                if storage == ExternalLongValueStorage::SinglePage {
                    result.push(payload, budget)?;
                } else {
                    let mut bytes = [0_u8; PAGE_BYTES];
                    for (offset, fragment) in chained_fragments(payload).enumerate() {
                        let next = (offset + 1 < count).then(|| {
                            RowLocator::new(PageNumber::new(start + offset as u64 + 1), 0)
                        });
                        budget.charge_work_units(fragment.len() as u64)?;
                        let length = encode_chained_row(fragment, next, &mut bytes)
                            .map_err(|_| refusal(row, "chained payload encoding"))?;
                        result.push(&bytes[..length], budget)?;
                    }
                }
            }
        }
        Ok(result)
    }

    fn push(&mut self, bytes: &[u8], budget: &mut ResourceBudget) -> Result<(), ComposeError> {
        let mut builder = DataPageBuilder::new_long_value(budget)?;
        builder.append_row(bytes, budget)?;
        // Candidate policy: a page is available if another nonempty row fits.
        let available = match builder.clone().append_row(&[0], budget) {
            Ok(_) => true,
            Err(PageImageError::PageFull { .. } | PageImageError::RowSlotsExhausted { .. }) => {
                false
            }
            Err(error) => return Err(error.into()),
        };
        self.pages
            .push((finish_data_builder(builder, budget)?, available));
        Ok(())
    }

    pub(super) fn page_count(&self) -> u64 {
        self.pages.len() as u64
    }

    pub(super) fn append_pages(
        &self,
        plan: &mut WholeFileImagePlan,
        map: &mut InlineUsageMapEncoder,
        budget: &mut ResourceBudget,
    ) -> Result<(), ComposeError> {
        for (image, _) in &self.pages {
            plan.append(image.clone(), map, budget)?;
        }
        Ok(())
    }

    pub(super) fn maps(&self, budget: &mut ResourceBudget) -> Result<[[u8; 133]; 2], ComposeError> {
        let mut owned = InlineUsageMapEncoder::new(
            PageNumber::new(0),
            ByteCount::new(MAP_BITMAP_BYTES),
            budget,
        )?;
        let mut available = InlineUsageMapEncoder::new(
            PageNumber::new(0),
            ByteCount::new(MAP_BITMAP_BYTES),
            budget,
        )?;
        for (offset, (_, free)) in self.pages.iter().enumerate() {
            let page = PageNumber::new(self.first + offset as u64);
            owned.set_page(page)?;
            if *free {
                available.set_page(page)?;
            }
        }
        let mut rows = [[0_u8; 133]; 2];
        owned.encode_into(&mut rows[0], budget)?;
        available.encode_into(&mut rows[1], budget)?;
        Ok(rows)
    }
}
