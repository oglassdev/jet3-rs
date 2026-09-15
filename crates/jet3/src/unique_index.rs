//! Complete unique Long index mutations using EXP-0062/0126/0219.
use crate::index_key_page::{RECORD_BYTES, encode_record, locator};
use crate::page_edits::{PageEdits, reserve};
use crate::{
    ColumnOrdinal, ColumnPhysicalType, DatabaseReader, FileSource, IndexDirection, MapRowLocator,
    PAGE_BYTES, PageImage, PageNumber, ResourceBudget, RowLocator, TableDefinition, UpdateError,
};

pub(crate) struct UniqueIndex {
    pub column: ColumnOrdinal,
    direction: IndexDirection,
    records: Vec<[u8; RECORD_BYTES]>,
    mapped: Vec<PageNumber>,
    changed: bool,
}

pub(crate) fn load(
    database: &mut DatabaseReader<FileSource>,
    table: &TableDefinition,
    budget: &mut ResourceBudget,
) -> Result<UniqueIndex, UpdateError> {
    let ([physical], [_]) = (table.physical_indexes(), table.indexes()) else {
        return Err(UpdateError::Unsupported(
            "mutation requires one unique Long index",
        ));
    };
    let [key] = physical.fields() else {
        return Err(UpdateError::Unsupported("mutation requires one Long key"));
    };
    let column = table
        .columns()
        .get(usize::from(key.column().get()))
        .ok_or(UpdateError::NotFound("key column"))?;
    if !physical.unique()
        || column.auto_increment()
        || column.physical_type() != ColumnPhysicalType::Long
    {
        return Err(UpdateError::Unsupported(
            "mutation requires ordinary unique Long",
        ));
    }
    let tree = database.index_tree(table, 0, budget)?;
    crate::unique_index_structure::validate(database, table, &tree, budget)?;
    let mut records = Vec::new();
    reserve(&mut records, tree.entries().len(), budget)?;
    for entry in tree.entries() {
        let key: [u8; 5] = entry
            .key()
            .raw_bytes()
            .try_into()
            .map_err(|_| UpdateError::Unsupported("null or non-Long index key"))?;
        records.push(encode_record(key, entry.row())?);
    }
    let mut expected = Vec::new();
    reserve(&mut expected, records.len(), budget)?;
    let mut cursor = database.rows(table, budget)?;
    while let Some(row) = cursor.next_row()? {
        if row.locator() != row.storage_locator() {
            return Err(UpdateError::Unsupported("overflow indexed row"));
        }
        if expected.len() == records.len() {
            return Err(UpdateError::Mismatch("missing index entries"));
        }
        let raw: [u8; 4] = row
            .field(key.column())
            .and_then(|f| f.raw_bytes())
            .ok_or(UpdateError::Unsupported("null indexed row"))?
            .try_into()
            .map_err(|_| UpdateError::Mismatch("Long row width"))?;
        expected.push(encode_record(
            crate::long_index_key::encode(i32::from_le_bytes(raw), key.direction()),
            row.locator(),
        )?);
    }
    drop(cursor);
    budget.charge_work_units(
        (expected.len() as u64)
            .saturating_mul((expected.len().max(1).ilog2() + 1) as u64)
            .saturating_mul(4 * RECORD_BYTES as u64),
    )?;
    expected.sort_unstable();
    if records != expected || records.windows(2).any(|w| w[0][..5] >= w[1][..5]) {
        return Err(UpdateError::Mismatch(
            "unique index row/key/locator correspondence",
        ));
    }
    let mut before = [0; PAGE_BYTES];
    database.read_raw_page(table.root(), &mut before, budget)?;
    crate::index_key_page::check_counts(&before, physical.sourced_prefix(), records.len())?;
    let location = physical.usage_map();
    let page = database
        .read_classified_page(location.page(), &mut before, budget)
        .map_err(crate::TableDefinitionError::Page)?;
    let row = crate::locate_usage_map(
        page,
        MapRowLocator::new(location.page(), location.row()),
        budget,
    )
    .map_err(UpdateError::UsageMap)?;
    let crate::AllocationMap::Inline(map) =
        crate::decode_allocation_map(row.raw(), budget).map_err(UpdateError::Allocation)?
    else {
        return Err(UpdateError::Unsupported("indirect index map"));
    };
    let mut allocated = map.allocated_pages(database.geometry());
    let mut mapped = Vec::new();
    while let Some(page) = allocated
        .next_page(budget)
        .map_err(UpdateError::Allocation)?
    {
        reserve(&mut mapped, 1, budget)?;
        mapped.push(page);
    }
    budget.charge_work_units(
        (tree.nodes().len() as u64).saturating_mul((mapped.len().max(1).ilog2() + 1) as u64),
    )?;
    if tree
        .nodes()
        .iter()
        .any(|n| mapped.binary_search(&n.page()).is_err())
    {
        return Err(UpdateError::Mismatch("index page absent from map"));
    }
    // Reuse reserved pages only when their retained header identifies this index's table.
    let owner = u32::try_from(table.root().get())
        .map_err(|_| UpdateError::Mismatch("index owner width"))?;
    for page in &mapped {
        database.read_raw_page(*page, &mut before, budget)?;
        if !matches!(before[0], 3 | 4) || before[1] != 1 || before[4..8] != owner.to_le_bytes() {
            return Err(UpdateError::Mismatch("mapped index page kind or owner"));
        }
    }
    Ok(UniqueIndex {
        column: key.column(),
        direction: key.direction(),
        records,
        mapped,
        changed: false,
    })
}

impl UniqueIndex {
    pub fn insert(
        &mut self,
        value: i32,
        row: RowLocator,
        budget: &mut ResourceBudget,
    ) -> Result<(), UpdateError> {
        let key = crate::long_index_key::encode(value, self.direction);
        budget.charge_work_units(self.records.len() as u64 * RECORD_BYTES as u64)?;
        let position = self.records.partition_point(|r| r[..5] < key[..]);
        if self.records.get(position).is_some_and(|r| r[..5] == key) {
            return Err(UpdateError::Unsupported("duplicate unique key"));
        }
        reserve(&mut self.records, 1, budget)?;
        self.records.insert(position, encode_record(key, row)?);
        self.changed = true;
        Ok(())
    }

    pub fn remove(
        &mut self,
        row: RowLocator,
        budget: &mut ResourceBudget,
    ) -> Result<(), UpdateError> {
        budget.charge_work_units(self.records.len() as u64 * (RECORD_BYTES + 1) as u64)?;
        let position = self
            .records
            .iter()
            .position(|r| locator(r) == Some(row))
            .ok_or(UpdateError::NotFound("indexed row"))?;
        self.records.remove(position);
        self.changed = true;
        Ok(())
    }

    pub fn replace(
        &mut self,
        row: RowLocator,
        value: i32,
        budget: &mut ResourceBudget,
    ) -> Result<(), UpdateError> {
        let key = crate::long_index_key::encode(value, self.direction);
        budget.charge_work_units(self.records.len() as u64 * RECORD_BYTES as u64)?;
        if self
            .records
            .iter()
            .any(|r| locator(r) == Some(row) && r[..5] == key)
        {
            return Ok(());
        }
        self.remove(row, budget)?;
        self.insert(value, row, budget)
    }

    pub fn stage(
        &self,
        database: &mut DatabaseReader<FileSource>,
        table: &TableDefinition,
        edits: &mut PageEdits,
        budget: &mut ResourceBudget,
    ) -> Result<(), UpdateError> {
        if !self.changed {
            return Ok(());
        }
        let physical = &table.physical_indexes()[0];
        let root = physical.root();
        let layout = crate::long_index_pages::LongIndexPages::new(self.records.len(), budget)?;
        let mut pages = Vec::new();
        reserve(&mut pages, layout.len(), budget)?;
        pages.extend(
            self.mapped
                .iter()
                .copied()
                .filter(|p| *p != root)
                .take(layout.len() - 1),
        );
        while pages.len() < layout.len() - 1 {
            let page = edits.append(PageImage::from_bytes([0; PAGE_BYTES]), budget)?;
            let location = physical.usage_map();
            let map = MapRowLocator::new(location.page(), location.row());
            let global = MapRowLocator::new(PageNumber::new(1), 0);
            if map == global {
                return Err(UpdateError::Mismatch("aliased global/index map"));
            }
            edits.map_bit(database, global, page, true, false, budget)?;
            edits.map_bit(database, map, page, false, true, budget)?;
            pages.push(page);
        }
        pages.push(root);
        for (ordinal, page) in pages.iter().enumerate() {
            let mut before = [0; PAGE_BYTES];
            if page.get() < database.geometry().page_count() {
                database.read_raw_page(*page, &mut before, budget)?;
            }
            let image = layout.image(
                ordinal,
                &self.records,
                &pages,
                table.root(),
                &before,
                budget,
            )?;
            edits.set_image(database, *page, image, budget)?;
        }
        Ok(())
    }
}
