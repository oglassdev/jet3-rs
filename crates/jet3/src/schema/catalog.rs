//! Catalog field edits through the sourced EXP-0073 system schemas and row planners.
use crate::{
    ColumnOrdinal, DatabaseReader, FileSource, PageNumber, ResourceBudget, RowLocator, RowValue,
    TableDefinition, UpdateError,
    write::page_edits::{PageEdits, reserve},
};
use std::fs::File;

pub(crate) fn table(
    database: &mut DatabaseReader<FileSource>,
    name: &[u8],
    budget: &mut ResourceBudget,
) -> Result<TableDefinition, UpdateError> {
    let root = {
        let mut catalog = database.catalog(budget)?;
        let mut root = None;
        while let Some(record) = catalog.next_record()? {
            if record.name().raw_bytes() == name {
                if root.is_some() {
                    return Err(UpdateError::Mismatch("ambiguous catalog table"));
                }
                root = record.table_definition();
            }
        }
        root.ok_or(UpdateError::NotFound("catalog table"))?
    };
    Ok(database.table_definition(root, budget)?)
}

pub(crate) fn column(table: &TableDefinition, name: &[u8]) -> Result<ColumnOrdinal, UpdateError> {
    table
        .columns()
        .iter()
        .find(|column| column.name().raw_bytes() == name)
        .map(|c| c.ordinal())
        .ok_or(UpdateError::NotFound("catalog column"))
}

pub(crate) fn object(
    database: &mut DatabaseReader<FileSource>,
    catalog: &TableDefinition,
    root: PageNumber,
    budget: &mut ResourceBudget,
) -> Result<RowLocator, UpdateError> {
    let id = column(catalog, b"Id")?;
    let mut rows = database.rows(catalog, budget)?;
    let mut found = None;
    while let Some(mut row) = rows.next_row()? {
        if matches!(crate::row::scalar_values::read_column(&mut row, id)?, RowValue::Long(value) if i64::from(value) == root.get() as i64)
        {
            if found.is_some() {
                return Err(UpdateError::Mismatch("duplicate catalog object"));
            }
            found = Some(row.locator());
        }
    }
    found.ok_or(UpdateError::NotFound("catalog object"))
}

pub(crate) fn rename_table(
    file: &mut File,
    journal: &mut PageEdits,
    old: &[u8],
    new: &[u8],
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    let (relationships, updates) =
        crate::schema::edit::apply(file, journal, budget, |database, budget| {
            let order = database.header().sort_order();
            let selected = crate::write::update::indexed_writable_table(database, old, budget)?;
            crate::schema::table::validate_name(database, new, Some(selected.root()), budget)?;
            crate::relationship::catalog::validate(database, budget)?;
            let relationships = table(database, b"MSysRelationships", budget)?;
            let fields = [
                column(&relationships, b"szObject")?,
                column(&relationships, b"szReferencedObject")?,
            ];
            let mut updates = Vec::new();
            let mut rows = database.rows(&relationships, budget)?;
            while let Some(mut row) = rows.next_row()? {
                for field in fields {
                    row.budget_mut().charge_work_units(1024)?;
                    if row
                        .field(field)
                        .and_then(|field| field.raw_bytes())
                        .is_some_and(|name| {
                            crate::catalog::name_key::catalog_names_equal_for(order, name, old)
                        })
                    {
                        let locator = row.locator();
                        reserve(&mut updates, 1, row.budget_mut())?;
                        updates.push((locator, field));
                    }
                }
            }
            drop(rows);
            let catalog = table(database, b"MSysObjects", budget)?;
            let row = object(database, &catalog, selected.root(), budget)?;
            let name = column(&catalog, b"Name")?;
            let graph =
                crate::row::mutation_graph::RowGraph::load(database, &catalog, Some(row), budget)?;
            let edits = crate::write::field_update::plan_fields(
                database,
                &catalog,
                graph,
                row,
                &[(name, RowValue::Text(new))],
                budget,
            )?;
            Ok((edits, (relationships.root(), updates)))
        })?;
    for (row, column) in updates {
        crate::schema::edit::apply(file, journal, budget, |database, budget| {
            let definition = database.table_definition(relationships, budget)?;
            let graph = crate::row::mutation_graph::RowGraph::load(
                database,
                &definition,
                Some(row),
                budget,
            )?;
            let edits = crate::write::field_update::plan_fields(
                database,
                &definition,
                graph,
                row,
                &[(column, RowValue::Text(new))],
                budget,
            )?;
            Ok((edits, ()))
        })?;
    }
    crate::schema::edit::apply(file, journal, budget, |database, budget| {
        crate::relationship::catalog::validate(database, budget)?;
        Ok((PageEdits::new(database.geometry().page_count()), ()))
    })
}
