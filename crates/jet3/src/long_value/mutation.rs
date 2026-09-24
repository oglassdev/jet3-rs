//! Memo/OLE mutation composes EXP-0061 payloads and EXP-0077 column maps.
//! EXP-0234 separates single/chained pools; EXP-0235 permits empty deleted
//! sibling slots. The inline cutoff and packing remain writer policies.
use crate::{
    ColumnOrdinal, ColumnPhysicalType, DatabaseReader, ExternalLongValueStorage, FileSource,
    LongValueMapDefinition, MapRowLocator, PAGE_BYTES, PageImage, PageNumber, PageOffset,
    ResourceBudget, RowColumnLayout, RowLocator, RowValue, TableDefinition, UpdateError,
    long_value::writer::{
        HEADER_LEN, MAX_CHAINED_FRAGMENT, MAX_SINGLE_PAGE_PAYLOAD,
        MAX_SINGLE_PAGE_PROPERTY_PAYLOAD, encode_chained_row, encode_inline_long_value,
        external_long_value_header,
    },
    write::page_edits::{PageEdits, reserve},
};

const INLINE_LIMIT: usize = 32;
pub(super) const OWNER: PageNumber = PageNumber::new(u32::from_le_bytes(*b"LVAL") as u64);

pub(super) struct PayloadPage {
    pub(super) page: PageNumber,
    pub(super) original_column: Option<usize>,
    pub(super) column: Option<usize>,
    pub(super) original_available: bool,
    pub(super) storage: Option<ExternalLongValueStorage>,
    pub(super) image: PageImage,
    pub(super) seen: [u64; 4],
    pub(super) removed: [u64; 4],
    pub(super) changed: bool,
}

pub(crate) struct LongValues {
    pub(super) maps: Vec<LongValueMapDefinition>,
    pub(super) pages: Vec<PayloadPage>,
    pub(super) first_append: u64,
    pub(super) append_count: u64,
    /// The catalog `LvProp` column, stored under the `EXP-0300` single-page limit.
    pub(super) property_column: Option<usize>,
}

struct PayloadHeader {
    ordinal: usize,
    bytes: [u8; HEADER_LEN + INLINE_LIMIT],
    length: usize,
}

fn write_error(_: crate::PageImageError) -> UpdateError {
    UpdateError::Unsupported("long-value fragment does not fit a page")
}

impl LongValues {
    pub fn load(
        database: &mut DatabaseReader<FileSource>,
        table: &TableDefinition,
        selected: Option<RowLocator>,
        budget: &mut ResourceBudget,
    ) -> Result<Self, UpdateError> {
        super::mutation_load::load(database, table, selected.map(|row| (row, None)), budget)
    }

    pub fn load_fields(
        database: &mut DatabaseReader<FileSource>,
        table: &TableDefinition,
        row: RowLocator,
        columns: &[ColumnOrdinal],
        budget: &mut ResourceBudget,
    ) -> Result<Self, UpdateError> {
        super::mutation_load::load(database, table, Some((row, Some(columns))), budget)
    }

    pub fn remove_selected(&mut self, budget: &mut ResourceBudget) -> Result<(), UpdateError> {
        for page in &mut self.pages {
            budget.charge_work_units(256)?;
            for slot in (0..=u8::MAX).rev() {
                if page.removed[usize::from(slot) / 64] & (1 << (slot % 64)) == 0 {
                    continue;
                }
                let deletion = crate::row::delete_page::remove(
                    page.page,
                    OWNER,
                    page.image.as_bytes(),
                    slot,
                    budget,
                )?;
                if matches!(deletion, crate::row::delete_page::Deletion::Released(_)) {
                    page.column = None;
                    page.storage = None;
                }
                page.image = match deletion {
                    crate::row::delete_page::Deletion::Retained(image)
                    | crate::row::delete_page::Deletion::Released(image) => image,
                };
                page.changed = true;
            }
        }
        Ok(())
    }

    pub fn encode_row(
        &mut self,
        layout: &[RowColumnLayout],
        values: &[RowValue<'_>],
        output: &mut [u8],
        budget: &mut ResourceBudget,
    ) -> Result<usize, UpdateError> {
        self.encode(layout, values, None, output, budget)
    }

    /// Unselected descriptors must come from the validated source row.
    pub fn encode_fields_row(
        &mut self,
        layout: &[RowColumnLayout],
        values: &[RowValue<'_>],
        selected: &[ColumnOrdinal],
        output: &mut [u8],
        budget: &mut ResourceBudget,
    ) -> Result<usize, UpdateError> {
        self.encode(layout, values, Some(selected), output, budget)
    }

    fn encode(
        &mut self,
        layout: &[RowColumnLayout],
        values: &[RowValue<'_>],
        selected: Option<&[ColumnOrdinal]>,
        output: &mut [u8],
        budget: &mut ResourceBudget,
    ) -> Result<usize, UpdateError> {
        if values.len() != layout.len() || values.len() > u8::MAX as usize {
            return Ok(crate::encode_row(layout, values, output, budget)?.get() as usize);
        }
        let mut lowered = [RowValue::Null; u8::MAX as usize];
        lowered[..values.len()].copy_from_slice(values);
        let mut headers = Vec::new();
        reserve(&mut headers, self.maps.len(), budget)?;
        budget.charge_items(values.len() as u64)?;
        for (ordinal, value) in values.iter().enumerate() {
            let (payload, kind) = match value {
                RowValue::Memo(bytes) => (*bytes, ColumnPhysicalType::Memo),
                RowValue::LongBinary(bytes) => (*bytes, ColumnPhysicalType::LongBinary),
                RowValue::LongValue(_)
                    if selected.is_some_and(|columns| {
                        !columns.contains(&ColumnOrdinal::new(ordinal as u16))
                    }) =>
                {
                    continue;
                }
                RowValue::LongValue(_) => {
                    return Err(UpdateError::Unsupported(
                        "caller-supplied long-value header",
                    ));
                }
                _ => continue,
            };
            if layout[ordinal].physical_type() != kind {
                return Err(crate::RowWriteError::TypeMismatch {
                    ordinal: ordinal as u16,
                    physical_type: layout[ordinal].physical_type(),
                }
                .into());
            }
            // EXP-0200: empty OLE saves as null, without a long-value descriptor.
            if payload.is_empty() && kind == ColumnPhysicalType::LongBinary {
                lowered[ordinal] = RowValue::Null;
                continue;
            }
            super::mutation_map::payload_budget(payload.len(), budget)?;
            let column = self
                .maps
                .iter()
                .position(|m| usize::from(m.column().get()) == ordinal)
                .ok_or(UpdateError::Mismatch("missing long-value column map"))?;
            let mut header = PayloadHeader {
                ordinal,
                bytes: [0; HEADER_LEN + INLINE_LIMIT],
                length: HEADER_LEN,
            };
            if payload.len() <= INLINE_LIMIT {
                budget.charge_work_units((HEADER_LEN + payload.len()) as u64)?;
                header.length = encode_inline_long_value(payload, &mut header.bytes)
                    .map_err(|_| UpdateError::Unsupported("inline long-value encoding"))?;
            } else {
                // Validate the 24-bit declared length before reserving any fragments.
                let limit = if self.property_column == Some(ordinal) {
                    MAX_SINGLE_PAGE_PROPERTY_PAYLOAD
                } else {
                    MAX_SINGLE_PAGE_PAYLOAD
                };
                let storage = if payload.len() <= limit {
                    ExternalLongValueStorage::SinglePage
                } else {
                    ExternalLongValueStorage::Chained
                };
                external_long_value_header(
                    payload.len(),
                    storage,
                    RowLocator::new(PageNumber::new(1), 0),
                )
                .map_err(|_| UpdateError::Unsupported("long-value declared length"))?;
                let target = if storage == ExternalLongValueStorage::SinglePage {
                    self.place(column, storage, payload, budget)?
                } else {
                    budget.check_chain_depth(payload.len().div_ceil(MAX_CHAINED_FRAGMENT) as u64)?;
                    let mut next = None;
                    let mut bytes = [0; PAGE_BYTES];
                    for fragment in payload.chunks(MAX_CHAINED_FRAGMENT).rev() {
                        budget.charge_items(1)?;
                        budget.charge_work_units(fragment.len() as u64)?;
                        let length = encode_chained_row(fragment, next, &mut bytes)
                            .map_err(|_| UpdateError::Unsupported("long-value chain encoding"))?;
                        next = Some(self.place(column, storage, &bytes[..length], budget)?);
                    }
                    next.ok_or(UpdateError::Mismatch("empty long-value chain"))?
                };
                header.bytes[..HEADER_LEN].copy_from_slice(
                    &external_long_value_header(payload.len(), storage, target)
                        .map_err(|_| UpdateError::Unsupported("long-value reference encoding"))?,
                );
            }
            headers.push(header);
        }
        for header in &headers {
            lowered[header.ordinal] = RowValue::LongValue(&header.bytes[..header.length]);
        }
        Ok(crate::encode_row(layout, &lowered[..values.len()], output, budget)?.get() as usize)
    }

    fn place(
        &mut self,
        column: usize,
        storage: ExternalLongValueStorage,
        bytes: &[u8],
        budget: &mut ResourceBudget,
    ) -> Result<RowLocator, UpdateError> {
        for page in &mut self.pages {
            budget.charge_items(1)?;
            if page.column != Some(column)
                || page.storage != Some(storage)
                || storage == ExternalLongValueStorage::Chained
            {
                continue;
            }
            if let Some((image, slot)) = crate::row::insert_page::append(
                page.page,
                OWNER,
                page.image.as_bytes(),
                bytes,
                budget,
            )? {
                page.image = image;
                page.changed = true;
                return Ok(RowLocator::new(page.page, slot));
            }
        }
        let mut builder = crate::DataPageBuilder::new_long_value(budget).map_err(write_error)?;
        builder.append_row(bytes, budget).map_err(write_error)?;
        let free = u16::try_from(builder.free_bytes().get())
            .map_err(|_| UpdateError::Mismatch("long-value free bytes"))?;
        let mut image = builder.finish();
        let [lo, hi] = free.to_le_bytes();
        image.write_at(PageOffset::new(1), &[1, lo, hi], budget)?;
        budget.charge_work_units(self.pages.len() as u64)?;
        if let Some(page) = self.pages.iter_mut().find(|p| p.column.is_none()) {
            // Preserve slack while resetting the free page to one physical slot.
            let mut retained = page.image.clone();
            retained.write_at(PageOffset::new(0), &image.as_bytes()[..12], budget)?;
            retained.write_at(
                PageOffset::new((PAGE_BYTES - bytes.len()) as u64),
                bytes,
                budget,
            )?;
            page.image = retained;
            page.column = Some(column);
            page.storage = Some(storage);
            page.changed = true;
            return Ok(RowLocator::new(page.page, 0));
        }
        let number = self
            .first_append
            .checked_add(self.append_count)
            .filter(|page| *page <= 0x00ff_ffff)
            .ok_or(UpdateError::Unsupported("long-value page reference width"))?;
        self.append_count += 1;
        let page = PageNumber::new(number);
        reserve(&mut self.pages, 1, budget)?;
        self.pages.push(PayloadPage {
            page,
            original_column: None,
            column: Some(column),
            original_available: false,
            image,
            storage: Some(storage),
            seen: [0; 4],
            removed: [0; 4],
            changed: true,
        });
        Ok(RowLocator::new(page, 0))
    }

    pub fn stage(
        self,
        database: &mut DatabaseReader<FileSource>,
        edits: &mut PageEdits,
        budget: &mut ResourceBudget,
    ) -> Result<(), UpdateError> {
        let global = MapRowLocator::new(PageNumber::new(1), 0);
        for page in self.pages {
            if !page.changed {
                continue;
            }
            if page.page.get() >= self.first_append {
                if edits.append(page.image.clone(), budget)? != page.page {
                    return Err(UpdateError::Mismatch("long-value append placement"));
                }
            } else {
                edits.set_image(database, page.page, page.image.clone(), budget)?;
            }
            let available = page.column.is_some()
                && page.storage == Some(ExternalLongValueStorage::SinglePage)
                && crate::row::insert_page::has_capacity(page.image.as_bytes(), 1);
            budget.charge_items(self.maps.len() as u64)?;
            for (ordinal, map) in self.maps.iter().enumerate() {
                let before = page.original_column == Some(ordinal);
                let after = page.column == Some(ordinal);
                if !before && !after {
                    continue;
                }
                edits.map_bit(database, map.owned(), page.page, before, after, budget)?;
                edits.map_bit(
                    database,
                    map.available(),
                    page.page,
                    before && page.original_available,
                    after && available,
                    budget,
                )?;
            }
            edits.map_bit(
                database,
                global,
                page.page,
                page.original_column.is_none(),
                page.column.is_none(),
                budget,
            )?;
        }
        Ok(())
    }
}
