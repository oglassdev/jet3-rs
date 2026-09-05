//! Bounded key/row correlation; only fixed-size isolated-leaf replacement.
use crate::index_key_page::{MAX_ENTRIES, RECORD_BYTES};
use crate::{
    ColumnPhysicalType, DatabaseReader, FieldUpdate, FileSource, PAGE_BYTES, PageImage, PageNumber,
    ResourceBudget, RowValue, TableDefinition, UpdateError,
};

pub(crate) struct KeyChange {
    pub(crate) page: PageNumber,
    pub(crate) before: [u8; PAGE_BYTES],
    pub(crate) after: PageImage,
}

pub(crate) fn plan(
    database: &mut DatabaseReader<FileSource>,
    table: &TableDefinition,
    request: FieldUpdate<'_>,
    budget: &mut ResourceBudget,
) -> Result<Option<KeyChange>, UpdateError> {
    let mut indexed = false;
    for index in table.physical_indexes() {
        for key in index.fields() {
            budget.charge_items(1)?;
            indexed |= key.column() == request.column;
        }
    }
    if !indexed {
        return Ok(None);
    }
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
        .get(usize::from(request.column.get()))
        .ok_or(UpdateError::NotFound("column"))?;
    if !physical.unique()
        || column.auto_increment()
        || column.physical_type() != ColumnPhysicalType::Long
    {
        return Err(UpdateError::Unsupported(
            "key update requires unique ordinary Long",
        ));
    }
    let RowValue::Long(value) = request.value else {
        return Err(UpdateError::Unsupported("key update requires present Long"));
    };
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
    let replacement = crate::long_index_key::encode(value, key.direction());
    let mut records = [[0; RECORD_BYTES]; MAX_ENTRIES];
    let mut selected = None;
    for (ordinal, entry) in tree.entries().iter().enumerate() {
        budget.charge_items(1)?;
        let raw = crate::index_key_page::record(&before, ordinal)
            .ok_or(UpdateError::Mismatch("leaf record"))?;
        if entry.key().raw_bytes().len() != replacement.len()
            || &raw[..replacement.len()] != entry.key().raw_bytes()
            || crate::index_key_page::locator(raw) != Some(entry.row())
        {
            return Err(UpdateError::Mismatch("Long leaf framing"));
        }
        if ordinal > 0 && tree.entries()[ordinal - 1].key().raw_bytes() >= entry.key().raw_bytes() {
            return Err(UpdateError::Mismatch("unique key ordering"));
        }
        if entry.row() == request.row {
            if selected.replace(ordinal).is_some() {
                return Err(UpdateError::Mismatch("duplicate row locator"));
            }
        } else if entry.key().raw_bytes() == replacement {
            return Err(UpdateError::Unsupported("duplicate unique key"));
        }
        records[ordinal].copy_from_slice(raw);
    }
    let selected = selected.ok_or(UpdateError::NotFound("indexed row"))?;
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
            .field(request.column)
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
    records[selected][..replacement.len()].copy_from_slice(&replacement);
    budget.charge_work_units((count * count) as u64)?;
    records[..count].sort_unstable();
    let after = crate::index_key_page::replace(&before, &records[..count], budget)?;
    Ok(Some(KeyChange {
        page: physical.root(),
        before,
        after,
    }))
}
