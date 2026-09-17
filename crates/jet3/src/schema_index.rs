//! Existing index edits using EXP-0059/0062/0148/0279/0297 metadata and tree layouts.
use crate::column_definition_writer::{write_logical_record, write_physical_record};
use crate::index_mutation::{entry_error, tree_error};
use crate::numeric_index_entry::{NumericIndexEntry, NumericIndexField, sort_cost};
use crate::numeric_index_key::NumericKeyType;
use crate::numeric_index_pages::NumericIndexPages;
use crate::page_edits::{PageEdits, reserve};
use crate::schema_definition::{DefinitionEdit, NamedRecord, allocate};
use crate::{
    BinaryWriter, ColumnRef, DatabaseReader, FileSource, IndexDefinitionKind, IndexFieldSpec,
    IndexNullPolicy, IndexSpec, LogicalIndexSpec, MapRowLocator, PAGE_BYTES, PageImage,
    PhysicalIndexSpec, ResourceBudget, SchemaEdit, TableDefinition, UpdateError,
};

#[cfg(all(test, any(unix, windows)))]
#[path = "schema_index_tests.rs"]
mod tests;

pub(crate) fn plan(
    database: &mut DatabaseReader<FileSource>,
    table: &TableDefinition,
    request: SchemaEdit<'_>,
    budget: &mut ResourceBudget,
) -> Result<PageEdits, UpdateError> {
    // Check every existing tree before changing its logical or physical inventory.
    if !table.physical_indexes().is_empty() {
        crate::index_mutation::load(database, table, budget)?;
    }
    let mut edits = PageEdits::new(database.geometry().page_count());
    let mut definition = DefinitionEdit::new(table, budget)?;
    match request {
        SchemaEdit::ReplaceIndex { .. }
        | SchemaEdit::RenameTable { .. }
        | SchemaEdit::CreateTable { .. }
        | SchemaEdit::RenameColumn { .. }
        | SchemaEdit::CreateColumn { .. }
        | SchemaEdit::DropTable { .. }
        | SchemaEdit::DropColumn { .. }
        | SchemaEdit::SetColumnOptions { .. }
        | SchemaEdit::DropRelationship { .. }
        | SchemaEdit::CreateRelationship { .. }
        | SchemaEdit::ReplaceRelationship { .. } => {
            return Err(UpdateError::Mismatch("index edit request"));
        }
        SchemaEdit::CreateIndex { index, .. } => {
            crate::schema_edit::name(index.name, 63)?;
            create(database, table, index, &mut definition, &mut edits, budget)?;
        }
        SchemaEdit::DropIndex { index, .. } => {
            drop_index(database, table, index, &mut definition, &mut edits, budget)?
        }
        SchemaEdit::RenameIndex { index, name, .. } => {
            let position = position(table, index)?;
            crate::schema_edit::name(name, 63)?;
            crate::schema_edit::distinct(
                name,
                table
                    .indexes()
                    .iter()
                    .enumerate()
                    .filter(|(n, _)| *n != position)
                    .map(|(_, index)| index.name().raw_bytes()),
                budget,
            )?;
            definition.indexes[position].name = name;
        }
    }
    sort_names(&mut definition, budget)?;
    definition.stage(database, table, &mut edits, budget)?;
    Ok(edits)
}

fn position(table: &TableDefinition, name: &[u8]) -> Result<usize, UpdateError> {
    let position = table
        .indexes()
        .iter()
        .position(|index| index.name().raw_bytes() == name)
        .ok_or(UpdateError::NotFound("index"))?;
    if matches!(
        table.indexes()[position].kind(),
        IndexDefinitionKind::Relationship(_)
    ) {
        return Err(UpdateError::Unsupported(
            "edit relationship through its relationship",
        ));
    }
    Ok(position)
}

pub(crate) fn sort_names(
    definition: &mut DefinitionEdit<'_>,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    let mut keys = Vec::new();
    reserve(&mut keys, definition.indexes.len(), budget)?;
    for index in &definition.indexes {
        budget.charge_work_units(1024)?;
        keys.push(
            crate::catalog_name_key::NameKey::new(index.name)
                .map_err(|_| UpdateError::Unsupported("index name collation"))?,
        );
    }
    // At most 32 logical names; move the records together with their collation keys.
    budget.charge_work_units((keys.len() as u64).pow(2) * 512)?;
    for index in 1..keys.len() {
        let mut at = index;
        while at > 0 && keys[at].bytes() < keys[at - 1].bytes() {
            keys.swap(at, at - 1);
            definition.indexes.swap(at, at - 1);
            at -= 1;
        }
    }
    Ok(())
}

pub(crate) fn create<'a>(
    database: &mut DatabaseReader<FileSource>,
    table: &'a TableDefinition,
    index: IndexSpec<'a>,
    definition: &mut DefinitionEdit<'a>,
    edits: &mut PageEdits,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    if !crate::creation::relationship_name::HiddenName::matches(index.name) {
        crate::schema_edit::name(index.name, 63)?;
    }
    crate::schema_edit::distinct(
        index.name,
        table.indexes().iter().map(|i| i.name().raw_bytes()),
        budget,
    )?;
    if table.indexes().len() >= crate::creation::schema_plan::MAX_OBSERVED_INDEXES
        || !(1..=10).contains(&index.fields.len())
    {
        return Err(UpdateError::Unsupported("index or key field capacity"));
    }
    if index.kind.is_primary()
        && (index.kind.null_policy() != IndexNullPolicy::Required
            || table
                .indexes()
                .iter()
                .any(|i| i.kind() == IndexDefinitionKind::Primary))
    {
        return Err(UpdateError::Unsupported(
            "primary index options or duplicate primary",
        ));
    }
    let mut fields = Vec::new();
    let mut numeric = Vec::new();
    let mut selected = [false; 255];
    reserve(&mut fields, index.fields.len(), budget)?;
    reserve(&mut numeric, index.fields.len(), budget)?;
    for field in index.fields {
        let column = match field.column {
            ColumnRef::Ordinal(ordinal) => table.columns().get(usize::from(ordinal)),
            ColumnRef::Name(name) => table
                .columns()
                .iter()
                .find(|c| c.name().raw_bytes() == name),
        }
        .ok_or(UpdateError::NotFound("index column"))?;
        let ordinal = column.ordinal().get();
        let marked = selected
            .get_mut(usize::from(ordinal))
            .ok_or(UpdateError::Unsupported("column count"))?;
        if *marked {
            return Err(UpdateError::Unsupported("repeated index column"));
        }
        *marked = true;
        let kind = NumericKeyType::from_definition(column)
            .ok_or(UpdateError::Unsupported("index column type"))?;
        fields.push(IndexFieldSpec {
            column: column.storage_ordinal(),
            direction: field.direction,
        });
        numeric.push(NumericIndexField {
            column: usize::from(ordinal),
            direction: field.direction,
            kind,
        });
    }
    let alias = table.physical_indexes().iter().position(|physical| {
        physical.raw_flags() == index.kind.flags().raw()
            && physical.fields().len() == fields.len()
            && physical.fields().iter().zip(&numeric).all(|(a, b)| {
                usize::from(a.column().get()) == b.column && a.direction() == b.direction
            })
    });
    let physical = if let Some(alias) = alias {
        alias
    } else {
        let entries = entries(database, table, index, &numeric, &selected, budget)?;
        let layout = NumericIndexPages::new(&entries, usize::MAX, budget).map_err(tree_error)?;
        let mut pages = Vec::new();
        reserve(&mut pages, layout.len(), budget)?;
        for _ in 0..layout.len() {
            pages.push(allocate(
                database,
                edits,
                PageImage::from_bytes([0; PAGE_BYTES]),
                budget,
            )?);
        }
        let root = *pages
            .last()
            .ok_or(UpdateError::Mismatch("empty index page layout"))?;
        for (ordinal, &page) in pages.iter().enumerate() {
            let image = layout
                .image(
                    ordinal,
                    &entries,
                    |n| pages.get(n).copied(),
                    table.root(),
                    &[0; PAGE_BYTES],
                    budget,
                )
                .map_err(tree_error)?;
            edits.set_image(database, page, image, budget)?;
        }
        let map = crate::schema_map::create(database, edits, &pages, budget)?;
        let count = entries
            .iter()
            .enumerate()
            .filter(|(n, entry)| *n == 0 || entries[*n - 1].key() != entry.key())
            .count();
        let count =
            u32::try_from(count).map_err(|_| UpdateError::Unsupported("index distinct count"))?;
        let spec = PhysicalIndexSpec {
            fields: &fields,
            usage_map_page: map.page(),
            usage_map_row: map.row(),
            root,
            flags: index.kind.flags(),
            entry_count: count,
        };
        let mut raw = [0; 39];
        write_physical_record(&mut BinaryWriter::new(&mut raw, budget)?, &spec)?;
        let mut prefix = [0; 8];
        let entry_count = u32::try_from(entries.len())
            .map_err(|_| UpdateError::Unsupported("index entry count"))?;
        prefix[..4].copy_from_slice(&entry_count.to_le_bytes());
        prefix[4..].copy_from_slice(&count.to_le_bytes());
        reserve(&mut definition.physical, 1, budget)?;
        definition.physical.push((prefix, raw));
        definition.physical.len() - 1
    };
    let mut raw = [0; 20];
    write_logical_record(
        &mut BinaryWriter::new(&mut raw, budget)?,
        &LogicalIndexSpec {
            name: index.name,
            physical_index: physical as u16,
            kind: index.kind.logical_kind(),
        },
    )?;
    // EXP-0297: the first free logical identity is independent of physical order.
    let mut selector = 0_u32;
    while definition
        .indexes
        .iter()
        .any(|index| index.record[..4] == selector.to_le_bytes())
    {
        budget.charge_items(definition.indexes.len() as u64)?;
        selector = selector
            .checked_add(1)
            .ok_or(UpdateError::Unsupported("logical index identity capacity"))?;
    }
    raw[..4].copy_from_slice(&selector.to_le_bytes());
    reserve(&mut definition.indexes, 1, budget)?;
    definition.indexes.push(NamedRecord {
        record: raw,
        name: index.name,
    });
    Ok(())
}

fn entries(
    database: &mut DatabaseReader<FileSource>,
    table: &TableDefinition,
    index: IndexSpec<'_>,
    numeric: &[NumericIndexField],
    selected: &[bool; 255],
    budget: &mut ResourceBudget,
) -> Result<Vec<NumericIndexEntry>, UpdateError> {
    let mut entries = Vec::new();
    let mut rows = database.rows(table, budget)?;
    let mut count = 0_u32;
    while let Some(mut row) = rows.next_row()? {
        let locator = row.locator();
        let values = crate::numeric_row_values::read(&mut row, selected)?;
        if let Some(entry) = NumericIndexEntry::encode(
            numeric,
            &values,
            index.kind.null_policy(),
            locator,
            row.budget_mut(),
        )
        .map_err(entry_error)?
        {
            reserve(&mut entries, 1, row.budget_mut())?;
            entries.push(entry);
        }
        count = count
            .checked_add(1)
            .ok_or(UpdateError::Mismatch("row count overflow"))?;
    }
    drop(rows);
    if count != table.row_count() {
        return Err(UpdateError::Mismatch("table row count"));
    }
    budget.charge_work_units(
        (entries.len() as u64)
            .saturating_mul((entries.len().max(1).ilog2() + 1) as u64)
            .saturating_mul(sort_cost(numeric)),
    )?;
    entries.sort_unstable_by(|a, b| a.record().cmp(b.record()));
    if index.kind.is_unique()
        && entries
            .windows(2)
            .any(|pair| !pair[0].has_null() && pair[0].key() == pair[1].key())
    {
        return Err(UpdateError::Unsupported("duplicate unique key"));
    }
    Ok(entries)
}

fn drop_index(
    database: &mut DatabaseReader<FileSource>,
    table: &TableDefinition,
    name: &[u8],
    definition: &mut DefinitionEdit<'_>,
    edits: &mut PageEdits,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    let position = position(table, name)?;
    let ordinal = table.indexes()[position].physical_index();
    if table.indexes().iter().any(|index| {
        index.physical_index() == ordinal
            && matches!(index.kind(), IndexDefinitionKind::Relationship(relation) if relation.side() == crate::RelationshipSide::PrimaryTable)
    }) {
        return Err(UpdateError::Unsupported("unique index required by relationship"));
    }
    remove_position(database, table, position, definition, edits, budget)
}

pub(crate) fn remove_position(
    database: &mut DatabaseReader<FileSource>,
    table: &TableDefinition,
    position: usize,
    definition: &mut DefinitionEdit<'_>,
    edits: &mut PageEdits,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    let ordinal = table
        .indexes()
        .get(position)
        .ok_or(UpdateError::NotFound("index position"))?
        .physical_index();
    definition.indexes.remove(position);
    if table
        .indexes()
        .iter()
        .enumerate()
        .any(|(n, index)| n != position && index.physical_index() == ordinal)
    {
        return Ok(());
    }
    let physical = &table.physical_indexes()[usize::from(ordinal)];
    let map = MapRowLocator::new(physical.usage_map().page(), physical.usage_map().row());
    for page in crate::index_allocation::load(database, table.root(), map, budget)? {
        edits.map_bit(database, map, page, true, false, budget)?;
        edits.map_bit(
            database,
            crate::mutation_map_write::global_locator(),
            page,
            false,
            true,
            budget,
        )?;
    }
    definition.physical.remove(usize::from(ordinal));
    for index in &mut definition.indexes {
        let old = u32::from_le_bytes([
            index.record[4],
            index.record[5],
            index.record[6],
            index.record[7],
        ]);
        if old > u32::from(ordinal) {
            index.record[4..8].copy_from_slice(&(old - 1).to_le_bytes());
        }
    }
    Ok(())
}

pub(crate) fn edit(
    file: &mut std::fs::File,
    journal: &mut PageEdits,
    table: &[u8],
    request: SchemaEdit<'_>,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    let retired = crate::schema_publish::apply(file, journal, budget, |database, budget| {
        let definition = crate::update::indexed_writable_table(database, table, budget)?;
        let retired = if let SchemaEdit::DropIndex { index, .. } = request {
            let at = position(&definition, index)?;
            let physical = definition.indexes()[at].physical_index();
            if definition
                .indexes()
                .iter()
                .filter(|index| index.physical_index() == physical)
                .count()
                == 1
            {
                let map = definition.physical_indexes()[usize::from(physical)].usage_map();
                Some(MapRowLocator::new(map.page(), map.row()))
            } else {
                None
            }
        } else {
            None
        };
        let edits = plan(database, &definition, request, budget)?;
        Ok((edits, retired))
    })?;
    if let Some(map) = retired {
        crate::schema_map::retire(file, journal, map, budget)?;
    }
    Ok(())
}
