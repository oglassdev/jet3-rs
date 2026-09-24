//! Lossless definition edits using EXP-0059/0077 record layouts and EXP-0247 chains.
use crate::{
    DatabaseReader, FileSource, PAGE_BYTES, PageImage, PageNumber, PageOffset, ResourceBudget,
    TableDefinition, WriteError,
    definition::header::{LOGICAL_INDEX_COUNT, PHYSICAL_INDEX_COUNT},
    write::page_edits::{PageEdits, reserve},
};

pub(crate) struct NamedRecord<'a, const N: usize> {
    pub record: [u8; N],
    pub name: &'a [u8],
}

pub(crate) struct DefinitionEdit<'a> {
    pub header: [u8; crate::definition::header::DEFINITION_HEADER_LEN],
    pub columns: Vec<NamedRecord<'a, 18>>,
    pub physical: Vec<([u8; 8], [u8; 39])>,
    pub indexes: Vec<NamedRecord<'a, 20>>,
    pub suffix: Vec<u8>,
}

impl<'a> DefinitionEdit<'a> {
    pub fn new(
        table: &'a TableDefinition,
        budget: &mut ResourceBudget,
    ) -> Result<Self, WriteError> {
        let mut columns = Vec::new();
        let mut physical = Vec::new();
        let mut indexes = Vec::new();
        reserve(&mut columns, table.columns().len(), budget)?;
        reserve(&mut physical, table.physical_indexes().len(), budget)?;
        reserve(&mut indexes, table.indexes().len(), budget)?;
        columns.extend(table.columns().iter().map(|column| NamedRecord {
            record: *column.raw_record(),
            name: column.name().raw_bytes(),
        }));
        physical.extend(
            table
                .physical_indexes()
                .iter()
                .map(|index| (*index.sourced_prefix(), *index.raw_record())),
        );
        indexes.extend(table.indexes().iter().map(|index| NamedRecord {
            record: *index.raw_record(),
            name: index.name().raw_bytes(),
        }));
        let mut suffix = Vec::new();
        reserve(&mut suffix, table.raw_suffix().len(), budget)?;
        suffix.extend_from_slice(table.raw_suffix());
        Ok(Self {
            header: *table.raw_header(),
            columns,
            physical,
            indexes,
            suffix,
        })
    }

    fn encode(&mut self, budget: &mut ResourceBudget) -> Result<Vec<u8>, WriteError> {
        let logical = u16::try_from(self.indexes.len())
            .map_err(|_| WriteError::Unsupported("logical index count"))?;
        let physical = u16::try_from(self.physical.len())
            .map_err(|_| WriteError::Unsupported("physical index count"))?;
        self.header[LOGICAL_INDEX_COUNT..LOGICAL_INDEX_COUNT + 2]
            .copy_from_slice(&logical.to_le_bytes());
        self.header[PHYSICAL_INDEX_COUNT..PHYSICAL_INDEX_COUNT + 2]
            .copy_from_slice(&physical.to_le_bytes());
        let mut bytes = Vec::new();
        append(&mut bytes, &self.header, budget)?;
        for (prefix, _) in &self.physical {
            append(&mut bytes, prefix, budget)?;
        }
        for column in &self.columns {
            append(&mut bytes, &column.record, budget)?;
        }
        for column in &self.columns {
            name(&mut bytes, column.name, budget)?;
        }
        for (_, record) in &self.physical {
            append(&mut bytes, record, budget)?;
        }
        for index in &self.indexes {
            append(&mut bytes, &index.record, budget)?;
        }
        for index in &self.indexes {
            name(&mut bytes, index.name, budget)?;
        }
        append(&mut bytes, &self.suffix, budget)?;
        append(&mut bytes, &[0xff, 0xff], budget)?;
        let length =
            u32::try_from(bytes.len()).map_err(|_| WriteError::Unsupported("definition length"))?;
        bytes[8..12].copy_from_slice(&length.to_le_bytes());
        Ok(bytes)
    }

    pub fn stage(
        mut self,
        database: &mut DatabaseReader<FileSource>,
        table: &TableDefinition,
        edits: &mut PageEdits,
        budget: &mut ResourceBudget,
    ) -> Result<(), WriteError> {
        let bytes = self.encode(budget)?;
        stage_bytes(database, table.pages(), &bytes, edits, budget)
    }
}

pub(crate) fn stage_bytes(
    database: &mut DatabaseReader<FileSource>,
    original_pages: &[PageNumber],
    bytes: &[u8],
    edits: &mut PageEdits,
    budget: &mut ResourceBudget,
) -> Result<(), WriteError> {
    if original_pages.is_empty() {
        return Err(WriteError::Mismatch("definition root missing"));
    }
    let count = 1 + bytes
        .len()
        .saturating_sub(PAGE_BYTES)
        .div_ceil(PAGE_BYTES - 8);
    budget.check_chain_depth(count as u64)?;
    let mut pages = Vec::new();
    reserve(&mut pages, count, budget)?;
    pages.extend(original_pages.iter().copied().take(count));
    while pages.len() < count {
        pages.push(allocate(
            database,
            edits,
            PageImage::from_bytes([0; PAGE_BYTES]),
            budget,
        )?);
    }
    let mut offset = 0;
    for (ordinal, &page) in pages.iter().enumerate() {
        let mut original = [0; PAGE_BYTES];
        if page.get() < database.geometry().page_count() {
            database.read_raw_page(page, &mut original, budget)?;
        }
        let mut image = PageImage::from_bytes(original);
        let start = if ordinal == 0 { 0 } else { 8 };
        let length = (bytes.len() - offset).min(PAGE_BYTES - start);
        image.write_at(
            PageOffset::new(start as u64),
            &bytes[offset..offset + length],
            budget,
        )?;
        image.write_at(PageOffset::new(0), &[2, 1, 0x56, 0x43], budget)?;
        let next = pages.get(ordinal + 1).map_or(0, |page| page.get() as u32);
        image.write_at(PageOffset::new(4), &next.to_le_bytes(), budget)?;
        edits.set_image(database, page, image, budget)?;
        offset += length;
    }
    for &page in original_pages.iter().skip(count) {
        edits.map_bit(
            database,
            crate::alloc::mutation_map::global_locator(),
            page,
            false,
            true,
            budget,
        )?;
    }
    Ok(())
}

pub(crate) fn allocate(
    database: &mut DatabaseReader<FileSource>,
    edits: &mut PageEdits,
    image: PageImage,
    budget: &mut ResourceBudget,
) -> Result<PageNumber, WriteError> {
    let page = edits.append(image, budget)?;
    edits.map_bit(
        database,
        crate::alloc::mutation_map::global_locator(),
        page,
        true,
        false,
        budget,
    )?;
    Ok(page)
}

fn append(
    bytes: &mut Vec<u8>,
    value: &[u8],
    budget: &mut ResourceBudget,
) -> Result<(), WriteError> {
    reserve(bytes, value.len(), budget)?;
    budget.charge_work_units(value.len() as u64)?;
    bytes.extend_from_slice(value);
    Ok(())
}

fn name(bytes: &mut Vec<u8>, value: &[u8], budget: &mut ResourceBudget) -> Result<(), WriteError> {
    let length =
        u8::try_from(value.len()).map_err(|_| WriteError::Unsupported("definition name length"))?;
    append(bytes, &[length], budget)?;
    append(bytes, value, budget)
}
