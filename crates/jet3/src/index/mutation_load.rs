use super::mutation::*;
use crate::{
    DatabaseReader, FileSource, IndexNullPolicy, MapRowLocator, PAGE_BYTES, PageNumber,
    ResourceBudget, TableDefinition, UpdateError,
    alloc::mutation_map::MapBits,
    index::{
        entry::{ScalarIndexField, record_capacity, sort_cost},
        key::scalar::ScalarKeyType,
    },
    write::page_edits::reserve,
};

pub(crate) fn load(
    database: &mut DatabaseReader<FileSource>,
    table: &TableDefinition,
    budget: &mut ResourceBudget,
) -> Result<Indexes, UpdateError> {
    if !(1..=crate::create::schema_plan::MAX_OBSERVED_INDEXES)
        .contains(&table.physical_indexes().len())
        || table.columns().len() > u8::MAX as usize
    {
        return Err(UpdateError::Unsupported(
            "mutation requires one to 32 scalar indexes",
        ));
    }
    let mut result = Indexes {
        indexes: Vec::new(),
        columns: [false; u8::MAX as usize],
    };
    reserve(&mut result.indexes, table.physical_indexes().len(), budget)?;
    for (ordinal, physical) in (0_u16..).zip(table.physical_indexes()) {
        if !(1..=crate::index::entry::MAX_FIELDS).contains(&physical.fields().len()) {
            return Err(UpdateError::Unsupported(
                "mutation requires one to ten scalar key fields",
            ));
        }
        let mut fields = Vec::new();
        reserve(&mut fields, physical.fields().len(), budget)?;
        for field in physical.fields() {
            let ordinal = usize::from(field.column().get());
            let column = table
                .columns()
                .get(ordinal)
                .ok_or(UpdateError::NotFound("key column"))?;
            let kind = ScalarKeyType::from_definition(column)
                .ok_or(UpdateError::Unsupported("unsupported index key schema"))?;
            result.columns[ordinal] = true;
            fields.push(ScalarIndexField {
                column: ordinal,
                kind,
                direction: field.direction(),
            });
        }
        // EXP-0148/0166: user flag 02 omits only the all-null key.
        let null_policy = if physical.required() {
            IndexNullPolicy::Required
        } else if physical.raw_flags() & 2 != 0 {
            IndexNullPolicy::IgnoreAllNull
        } else {
            IndexNullPolicy::Include
        };
        let location = physical.usage_map();
        let map = MapRowLocator::new(location.page(), location.row());
        if map == MapRowLocator::new(PageNumber::new(1), 0)
            || map == table.maps().owned()
            || map == table.maps().available()
            || table.physical_indexes()[..usize::from(ordinal)]
                .iter()
                .any(|i| i.usage_map() == location)
        {
            return Err(UpdateError::Mismatch("aliased index allocation map"));
        }
        let mapped = mapped_index_pages(database, table.root(), map, budget)?;
        for previous in &result.indexes {
            budget.charge_work_units(
                (mapped.len() as u64)
                    .saturating_mul((previous.mapped.len().max(1).ilog2() + 1) as u64),
            )?;
            if mapped
                .iter()
                .any(|p| previous.mapped.binary_search(p).is_ok())
            {
                return Err(UpdateError::Mismatch("overlapping index page ownership"));
            }
        }
        budget.charge_work_units((table.indexes().len() as u64) * 2)?;
        let declared = table.indexes().iter().any(|index| {
            index.physical_index() == ordinal
                && matches!(
                    index.kind(),
                    crate::IndexDefinitionKind::Ordinary | crate::IndexDefinitionKind::Primary
                )
        });
        // EXP-0286: generated hidden parent trees share foreign assignment/removal counters.
        let relationship_counter = table.relationships().any(|relation| {
            relation.physical_index() == ordinal
                && (relation.side() == crate::RelationshipSide::ForeignTable || !declared)
        });
        result.indexes.push(MutableIndex {
            ordinal,
            fields,
            null_policy,
            unique: physical.unique(),
            entries: Vec::new(),
            mapped,
            changed: false,
            relationship_counter,
            counter: None,
        });
    }
    let mut cursor = database.rows(table, budget)?;
    let mut count = 0_u32;
    loop {
        cursor.owned.budget_mut().charge_items(u8::MAX as u64)?;
        let Some(mut row) = cursor.next_row()? else {
            break;
        };
        let locator = row.locator();
        let values = crate::row::scalar_values::read(&mut row, &result.columns)?;
        let budget = row.budget_mut();
        for index in &mut result.indexes {
            if let Some(entry) = index.encode(&values, locator, budget)? {
                reserve(&mut index.entries, 1, budget)?;
                index.entries.push(entry);
            }
        }
        count = count
            .checked_add(1)
            .ok_or(UpdateError::Mismatch("table row count overflow"))?;
    }
    drop(cursor);
    if table.row_count() != count {
        return Err(UpdateError::Mismatch("table row count"));
    }
    for index in &mut result.indexes {
        budget.charge_work_units(
            (index.entries.len() as u64)
                .saturating_mul((index.entries.len().max(1).ilog2() + 1) as u64)
                .saturating_mul(sort_cost(&index.fields)),
        )?;
        index
            .entries
            .sort_unstable_by(|a, b| a.record().cmp(b.record()));
        if index.unique
            && index
                .entries
                .windows(2)
                .any(|w| !w[0].has_null() && w[0].key() == w[1].key())
        {
            return Err(UpdateError::Mismatch("duplicate non-null unique index key"));
        }
        let tree = database.index_tree(table, index.ordinal, budget)?;
        crate::index::mutation_structure::validate(
            database,
            table,
            &tree,
            &index.fields,
            index.null_policy,
            budget,
        )?;
        budget.charge_work_units(
            (tree.nodes().len() as u64)
                .saturating_mul((index.mapped.len().max(1).ilog2() + 1) as u64)
                .saturating_add(index.entries.len() as u64 * record_capacity(&index.fields) as u64),
        )?;
        if tree
            .nodes()
            .iter()
            .any(|n| index.mapped.binary_search(&n.page()).is_err())
        {
            return Err(UpdateError::Mismatch("index page absent from map"));
        }
        if tree.entries().len() != index.entries.len()
            || tree
                .entries()
                .iter()
                .zip(&index.entries)
                .any(|(actual, expected)| {
                    actual.row() != expected.locator() || actual.key().raw_bytes() != expected.key()
                })
        {
            return Err(UpdateError::Mismatch(
                "index row/key/locator correspondence",
            ));
        }
    }
    Ok(result)
}

// EXP-0051/0057/0062: index ownership and global allocated membership.
pub(crate) fn mapped_index_pages(
    database: &mut DatabaseReader<FileSource>,
    root: PageNumber,
    location: MapRowLocator,
    budget: &mut ResourceBudget,
) -> Result<Vec<PageNumber>, UpdateError> {
    let map = MapBits::load(database, location, budget)?;
    let mapped = map.existing_pages(database.geometry().page_count(), false, budget)?;
    let global = MapBits::load(
        database,
        crate::alloc::mutation_map::global_locator(),
        budget,
    )?;
    if map.overlaps(&global, budget)? {
        return Err(UpdateError::Mismatch("aliased index and global maps"));
    }
    let mut bytes = [0; PAGE_BYTES];
    let owner =
        u32::try_from(root.get()).map_err(|_| UpdateError::Mismatch("index owner width"))?;
    for page in &mapped {
        budget.charge_items(1)?;
        if global.contains(*page)? {
            return Err(UpdateError::Mismatch("mapped index page is globally free"));
        }
        database.read_raw_page(*page, &mut bytes, budget)?;
        if !matches!(bytes[0], 3 | 4) || bytes[1] != 1 || bytes[4..8] != owner.to_le_bytes() {
            return Err(UpdateError::Mismatch("mapped index page kind or owner"));
        }
    }
    Ok(mapped)
}
