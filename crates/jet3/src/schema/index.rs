//! Existing index edits using EXP-0059/0062/0148/0279/0297 metadata and tree layouts.
use crate::{
    BinaryWriter, ColumnRef, DatabaseReader, FileSource, IndexDefinitionKind, IndexFieldSpec,
    IndexNullPolicy, IndexSpec, LogicalIndexSpec, MapRowLocator, PAGE_BYTES, PageImage,
    PhysicalIndexSpec, ResourceBudget, SchemaEdit, TableDefinition, WriteError,
    definition::column_writer::{write_logical_record, write_physical_record},
    index::{
        entry::{ScalarIndexEntry, ScalarIndexField, sort_cost},
        key::scalar::ScalarKeyType,
        mutation::{entry_error, tree_error},
        tree::builder::ScalarIndexPages,
    },
    schema::definition::{DefinitionEdit, NamedRecord, allocate},
    write::page_edits::{PageEdits, reserve},
};

pub(crate) fn plan(
    database: &mut DatabaseReader<FileSource>,
    table: &TableDefinition,
    request: SchemaEdit<'_>,
    budget: &mut ResourceBudget,
) -> Result<PageEdits, WriteError> {
    let order = database.header().sort_order();
    // Check every existing tree before changing its logical or physical inventory.
    if !table.physical_indexes().is_empty() {
        crate::index::mutation::load(database, table, budget)?;
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
        | SchemaEdit::SetColumnProperties { .. }
        | SchemaEdit::SetTableProperties { .. }
        | SchemaEdit::DropRelationship { .. }
        | SchemaEdit::CreateRelationship { .. }
        | SchemaEdit::ReplaceRelationship { .. } => {
            return Err(WriteError::Mismatch("index edit request"));
        }
        SchemaEdit::CreateIndex { index, .. } => {
            crate::schema::edit::name(order, index.name, 63)?;
            create(database, table, index, &mut definition, &mut edits, budget)?;
        }
        SchemaEdit::DropIndex { index, .. } => {
            drop_index(database, table, index, &mut definition, &mut edits, budget)?
        }
        SchemaEdit::RenameIndex { index, name, .. } => {
            let position = position(table, index)?;
            crate::schema::edit::name(order, name, 63)?;
            crate::schema::edit::distinct(
                order,
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
    sort_names(order, &mut definition, budget)?;
    definition.stage(database, table, &mut edits, budget)?;
    Ok(edits)
}

fn position(table: &TableDefinition, name: &[u8]) -> Result<usize, WriteError> {
    let position = table
        .indexes()
        .iter()
        .position(|index| index.name().raw_bytes() == name)
        .ok_or(WriteError::NotFound("index"))?;
    if matches!(
        table.indexes()[position].kind(),
        IndexDefinitionKind::Relationship(_)
    ) {
        return Err(WriteError::Unsupported(
            "edit relationship through its relationship",
        ));
    }
    Ok(position)
}

pub(crate) fn sort_names(
    order: crate::SortOrder,
    definition: &mut DefinitionEdit<'_>,
    budget: &mut ResourceBudget,
) -> Result<(), WriteError> {
    let mut keys = Vec::new();
    reserve(&mut keys, definition.indexes.len(), budget)?;
    for index in &definition.indexes {
        budget.charge_work_units(1024)?;
        keys.push(
            crate::catalog::name_key::NameKey::new(index.name, order)
                .map_err(|_| WriteError::Unsupported("index name collation"))?,
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
) -> Result<(), WriteError> {
    let order = database.header().sort_order();
    if !crate::create::relationship_name::HiddenName::matches(index.name) {
        crate::schema::edit::name(order, index.name, 63)?;
    }
    crate::schema::edit::distinct(
        order,
        index.name,
        table.indexes().iter().map(|i| i.name().raw_bytes()),
        budget,
    )?;
    if table.indexes().len() >= crate::create::schema_plan::MAX_OBSERVED_INDEXES
        || !(1..=10).contains(&index.fields.len())
    {
        return Err(WriteError::Unsupported("index or key field capacity"));
    }
    if index.kind.is_primary()
        && (index.kind.null_policy() != IndexNullPolicy::Required
            || table
                .indexes()
                .iter()
                .any(|i| i.kind() == IndexDefinitionKind::Primary))
    {
        return Err(WriteError::Unsupported(
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
        .ok_or(WriteError::NotFound("index column"))?;
        let ordinal = column.ordinal().get();
        let marked = selected
            .get_mut(usize::from(ordinal))
            .ok_or(WriteError::Unsupported("column count"))?;
        if *marked {
            return Err(WriteError::Unsupported("repeated index column"));
        }
        *marked = true;
        let kind = ScalarKeyType::from_definition(column)
            .ok_or(WriteError::Unsupported("index column type"))?;
        fields.push(IndexFieldSpec {
            column: column.storage_ordinal(),
            direction: field.direction,
        });
        numeric.push(ScalarIndexField {
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
        let layout = ScalarIndexPages::new(&entries, usize::MAX, budget).map_err(tree_error)?;
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
            .ok_or(WriteError::Mismatch("empty index page layout"))?;
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
        let map = crate::schema::map::create(database, edits, &pages, budget)?;
        let count = entries
            .iter()
            .enumerate()
            .filter(|(n, entry)| *n == 0 || entries[*n - 1].key() != entry.key())
            .count();
        let count =
            u32::try_from(count).map_err(|_| WriteError::Unsupported("index distinct count"))?;
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
            .map_err(|_| WriteError::Unsupported("index entry count"))?;
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
            .ok_or(WriteError::Unsupported("logical index identity capacity"))?;
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
    numeric: &[ScalarIndexField],
    selected: &[bool; 255],
    budget: &mut ResourceBudget,
) -> Result<Vec<ScalarIndexEntry>, WriteError> {
    let mut entries = Vec::new();
    let mut rows = database.rows(table, budget)?;
    let mut count = 0_u32;
    while let Some(mut row) = rows.next_row()? {
        let locator = row.locator();
        let values = crate::row::scalar_values::read(&mut row, selected)?;
        if let Some(entry) = ScalarIndexEntry::encode(
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
            .ok_or(WriteError::Mismatch("row count overflow"))?;
    }
    drop(rows);
    if count != table.row_count() {
        return Err(WriteError::Mismatch("table row count"));
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
        return Err(WriteError::Unsupported("duplicate unique key"));
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
) -> Result<(), WriteError> {
    let position = position(table, name)?;
    let ordinal = table.indexes()[position].physical_index();
    if table.indexes().iter().any(|index| {
        index.physical_index() == ordinal
            && matches!(index.kind(), IndexDefinitionKind::Relationship(relation) if relation.side() == crate::RelationshipSide::PrimaryTable)
    }) {
        return Err(WriteError::Unsupported("unique index required by relationship"));
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
) -> Result<(), WriteError> {
    let ordinal = table
        .indexes()
        .get(position)
        .ok_or(WriteError::NotFound("index position"))?
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
    for page in
        crate::index::mutation_load::mapped_index_pages(database, table.root(), map, budget)?
    {
        edits.map_bit(database, map, page, true, false, budget)?;
        edits.map_bit(
            database,
            crate::alloc::mutation_map::global_locator(),
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
) -> Result<(), WriteError> {
    let retired = crate::schema::edit::apply(file, journal, budget, |database, budget| {
        let definition = crate::write::update::indexed_writable_table(database, table, budget)?;
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
        crate::schema::map::retire(file, journal, map, budget)?;
    }
    Ok(())
}
