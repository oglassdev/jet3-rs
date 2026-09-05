//! Complete unique Long leaf correlation from EXP-0062/0073/0186.
use crate::index_key_page::{MAX_ENTRIES, RECORD_BYTES};
use crate::{
    ColumnOrdinal, ColumnPhysicalType, DatabaseReader, FileSource, IndexDirection, PAGE_BYTES,
    PageImage, PageNumber, ResourceBudget, RowLocator, TableDefinition, UpdateError,
};

pub(crate) struct UniqueLeaf {
    pub(crate) column: ColumnOrdinal,
    pub(crate) direction: IndexDirection,
    pub(crate) page: PageNumber,
    pub(crate) before: [u8; PAGE_BYTES],
    pub(crate) records: [[u8; RECORD_BYTES]; MAX_ENTRIES],
    pub(crate) count: usize,
}

pub(crate) fn validate(
    database: &mut DatabaseReader<FileSource>,
    table: &TableDefinition,
    budget: &mut ResourceBudget,
) -> Result<UniqueLeaf, UpdateError> {
    let ([physical], [_logical]) = (table.physical_indexes(), table.indexes()) else {
        return Err(UpdateError::Unsupported("key update requires one index"));
    };
    let [key] = physical.fields() else {
        return Err(UpdateError::Unsupported(
            "key update requires one Long field",
        ));
    };
    let column = table
        .columns()
        .get(usize::from(key.column().get()))
        .ok_or(UpdateError::NotFound("column"))?;
    if !physical.unique()
        || column.auto_increment()
        || column.physical_type() != ColumnPhysicalType::Long
    {
        return Err(UpdateError::Unsupported(
            "key update requires unique ordinary Long",
        ));
    }
    let mut before = [0; PAGE_BYTES];
    database.read_raw_page(physical.root(), &mut before, budget)?;
    let count = crate::index_key_page::validate(
        physical.root(),
        table.root(),
        database.geometry(),
        &before,
        budget,
    )?;
    let tree = database.index_tree(table, 0, budget)?;
    if count == 0 || count > MAX_ENTRIES || tree.nodes().len() != 1 || tree.entries().len() != count
    {
        return Err(UpdateError::Mismatch("single leaf entry inventory"));
    }
    let mut records = [[0; RECORD_BYTES]; MAX_ENTRIES];
    for (ordinal, entry) in tree.entries().iter().enumerate() {
        budget.charge_items(1)?;
        let raw = crate::index_key_page::record(&before, ordinal)
            .ok_or(UpdateError::Mismatch("leaf record"))?;
        if entry.key().raw_bytes().len() != 5
            || &raw[..5] != entry.key().raw_bytes()
            || crate::index_key_page::locator(raw) != Some(entry.row())
        {
            return Err(UpdateError::Mismatch("Long leaf framing"));
        }
        if ordinal > 0 && tree.entries()[ordinal - 1].key().raw_bytes() >= entry.key().raw_bytes() {
            return Err(UpdateError::Mismatch("unique key ordering"));
        }
        records[ordinal].copy_from_slice(raw);
    }
    let mut seen = [false; MAX_ENTRIES];
    let mut rows_count = 0;
    // At most 200 leaf entries; precharge the complete linear-search work per row.
    budget.charge_work_units((count * count) as u64)?;
    let mut cursor = database.rows(table, budget)?;
    while let Some(row) = cursor.next_row()? {
        if rows_count == count {
            return Err(UpdateError::Mismatch("row/index count"));
        }
        if row.locator() != row.storage_locator() {
            return Err(UpdateError::Unsupported("overflow indexed row"));
        }
        let raw = row
            .field(key.column())
            .and_then(|f| f.raw_bytes())
            .ok_or(UpdateError::Unsupported("null indexed field"))?;
        let raw: [u8; 4] = raw
            .try_into()
            .map_err(|_| UpdateError::Mismatch("Long field width"))?;
        let encoded = crate::long_index_key::encode(i32::from_le_bytes(raw), key.direction());
        let ordinal = tree
            .entries()
            .iter()
            .position(|entry| entry.row() == row.locator())
            .ok_or(UpdateError::Mismatch("unindexed live row"))?;
        if seen[ordinal] || tree.entries()[ordinal].key().raw_bytes() != encoded {
            return Err(UpdateError::Mismatch("key/row correlation"));
        }
        seen[ordinal] = true;
        rows_count += 1;
    }
    drop(cursor);
    if rows_count != count || seen[..count].contains(&false) {
        return Err(UpdateError::Mismatch("row/index bijection"));
    }
    let mut definition_page = [0; PAGE_BYTES];
    database.read_raw_page(table.root(), &mut definition_page, budget)?;
    crate::index_key_page::check_counts(&definition_page, physical.sourced_prefix(), count)?;
    Ok(UniqueLeaf {
        column: key.column(),
        direction: key.direction(),
        page: physical.root(),
        before,
        records,
        count,
    })
}

impl UniqueLeaf {
    // EXP-0057 inline membership must describe this complete one-page index.
    pub(crate) fn check_map(
        &self,
        database: &mut DatabaseReader<FileSource>,
        table: &TableDefinition,
        budget: &mut ResourceBudget,
    ) -> Result<(), UpdateError> {
        let location = table.physical_indexes()[0].usage_map();
        let mut bytes = [0; PAGE_BYTES];
        let page = database
            .read_classified_page(location.page(), &mut bytes, budget)
            .map_err(crate::TableDefinitionError::Page)?;
        let row = crate::locate_usage_map(
            page,
            crate::MapRowLocator::new(location.page(), location.row()),
            budget,
        )
        .map_err(UpdateError::UsageMap)?;
        let crate::AllocationMap::Inline(map) =
            crate::decode_allocation_map(row.raw(), budget).map_err(UpdateError::Allocation)?
        else {
            return Err(UpdateError::Unsupported("indirect index map"));
        };
        let mut pages = map.allocated_pages(database.geometry());
        if pages.next_page(budget).map_err(UpdateError::Allocation)? != Some(self.page)
            || pages
                .next_page(budget)
                .map_err(UpdateError::Allocation)?
                .is_some()
        {
            return Err(UpdateError::Mismatch("isolated index map"));
        }
        Ok(())
    }

    pub(crate) fn insert(
        &mut self,
        value: i32,
        row: RowLocator,
        budget: &mut ResourceBudget,
    ) -> Result<PageImage, UpdateError> {
        if self.count == MAX_ENTRIES {
            return Err(UpdateError::Unsupported("full root leaf"));
        }
        let key = crate::long_index_key::encode(value, self.direction);
        budget.charge_work_units((2 * (self.count + 2) * RECORD_BYTES) as u64)?;
        if self.records[..self.count].iter().any(|r| r[..5] == key) {
            return Err(UpdateError::Unsupported("duplicate unique key"));
        }
        let record = crate::index_key_page::encode_record(key, row)?;
        let position = self.records[..self.count].partition_point(|r| r < &record);
        self.records.copy_within(position..self.count, position + 1);
        self.records[position] = record;
        self.count += 1;
        crate::index_key_page::resize(&self.before, &self.records[..self.count], budget)
    }

    pub(crate) fn remove(
        &mut self,
        row: RowLocator,
        budget: &mut ResourceBudget,
    ) -> Result<PageImage, UpdateError> {
        if self.count <= 1 {
            return Err(UpdateError::Unsupported("last indexed row"));
        }
        budget.charge_work_units((2 * (self.count + 2) * RECORD_BYTES) as u64)?;
        let position = self.records[..self.count]
            .iter()
            .position(|r| crate::index_key_page::locator(r) == Some(row))
            .ok_or(UpdateError::NotFound("indexed row"))?;
        self.records.copy_within(position + 1..self.count, position);
        self.count -= 1;
        crate::index_key_page::resize(&self.before, &self.records[..self.count], budget)
    }
}
