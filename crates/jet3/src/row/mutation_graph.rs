//! EXP-0060 logical links and hidden storage, checked before physical mutation.
use crate::{
    DatabaseReader, PAGE_BYTES, PageKind, ReadAt, ResourceBudget, RowLocator, TableDefinition,
    WriteError, row::directory::RowDirectory, write::page_edits::reserve,
};

struct Record {
    locator: RowLocator,
    hidden: bool,
    target: Option<RowLocator>,
}

pub(crate) struct RowGraph {
    pub selected: Vec<RowLocator>,
    pub count: u32,
}

fn key(locator: RowLocator) -> (u64, u8) {
    (locator.page().get(), locator.slot())
}

impl RowGraph {
    pub fn load<S: ReadAt>(
        database: &mut DatabaseReader<S>,
        definition: &TableDefinition,
        selected: Option<RowLocator>,
        budget: &mut ResourceBudget,
    ) -> Result<Self, WriteError> {
        let owned =
            crate::alloc::mutation_map::MapBits::load(database, definition.maps().owned(), budget)?;
        let pages = owned.existing_pages(database.geometry().page_count(), false, budget)?;
        let mut records = Vec::new();
        let mut bytes = [0; PAGE_BYTES];
        for page in pages {
            let classified = database
                .read_classified_page(page, &mut bytes, budget)
                .map_err(|error| {
                    WriteError::Definition(crate::TableDefinitionError::Page(error))
                })?;
            if classified.kind() != PageKind::Data {
                return Err(WriteError::Mismatch("owned row page kind"));
            }
            let directory = RowDirectory::validate(page, definition.root(), &bytes, budget)?;
            budget.charge_items(u64::from(directory.row_count()))?;
            for slot in 0..directory.row_count() {
                let entry = directory.entry(&bytes, slot as u8)?;
                let range = entry.range();
                if range.is_empty() {
                    if entry.hidden() && entry.overflow() {
                        continue;
                    }
                    return Err(WriteError::Mismatch("empty live row slot"));
                }
                let target = if entry.overflow() {
                    let pointer = bytes[range]
                        .try_into()
                        .map_err(|_| WriteError::Mismatch("overflow pointer width"))?;
                    Some(crate::row::reader::decode_pointer(pointer))
                } else {
                    None
                };
                reserve(&mut records, 1, budget)?;
                records.push(Record {
                    locator: entry.locator(),
                    hidden: entry.hidden(),
                    target,
                });
            }
        }
        budget.charge_work_units(
            (records.len() as u64).saturating_mul(u64::from(records.len().max(1).ilog2()) + 1),
        )?;
        records.sort_unstable_by_key(|record| key(record.locator));
        let mut visited = Vec::new();
        reserve(&mut visited, records.len(), budget)?;
        visited.resize(records.len(), false);
        let mut result = Self {
            selected: Vec::new(),
            count: 0,
        };
        for (root, record) in records
            .iter()
            .enumerate()
            .filter(|(_, record)| !record.hidden)
        {
            result.count = result
                .count
                .checked_add(1)
                .ok_or(WriteError::Mismatch("table row count overflow"))?;
            let wanted = Some(record.locator) == selected;
            let mut current = root;
            let mut depth = 0;
            loop {
                budget.charge_work_units(1)?;
                if visited[current] {
                    return Err(WriteError::Mismatch("shared or cyclic row storage"));
                }
                visited[current] = true;
                if wanted {
                    reserve(&mut result.selected, 1, budget)?;
                    result.selected.push(records[current].locator);
                }
                let Some(target) = records[current].target else {
                    break;
                };
                depth += 1;
                budget.check_chain_depth(depth)?;
                budget.charge_work_units(u64::from(records.len().max(1).ilog2()) + 1)?;
                current = records
                    .binary_search_by_key(&key(target), |record| key(record.locator))
                    .map_err(|_| WriteError::Mismatch("missing owned overflow target"))?;
                if !records[current].hidden {
                    return Err(WriteError::Mismatch("overflow target is not hidden"));
                }
            }
        }
        budget.charge_work_units(visited.len() as u64)?;
        if visited.contains(&false) {
            return Err(WriteError::Mismatch("unreferenced hidden row storage"));
        }
        if result.count != definition.row_count() {
            return Err(WriteError::Mismatch("table row count"));
        }
        if selected.is_some() && result.selected.is_empty() {
            return Err(WriteError::NotFound("row"));
        }
        Ok(result)
    }
}
