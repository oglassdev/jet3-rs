//! Column renames preserve row storage and update EXP-0297 relationship names.
use crate::{
    ResourceBudget, RowValue, UpdateError,
    write::page_edits::{PageEdits, reserve},
};
use std::fs::File;

pub(crate) fn rename(
    file: &mut File,
    journal: &mut PageEdits,
    table: &[u8],
    old: &[u8],
    new: &[u8],
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    let (root, properties, relationships, updates) =
        crate::schema::edit::apply(file, journal, budget, |database, budget| {
            let order = database.header().sort_order();
            crate::schema::edit::name(order, new, 64)?;
            let definition = crate::write::update::indexed_writable_table(database, table, budget)?;
            let selected = definition
                .columns()
                .iter()
                .position(|column| column.name().raw_bytes() == old)
                .ok_or(UpdateError::NotFound("column"))?;
            crate::schema::edit::distinct(
                order,
                new,
                definition
                    .columns()
                    .iter()
                    .enumerate()
                    .filter(|(ordinal, _)| *ordinal != selected)
                    .map(|(_, column)| column.name().raw_bytes()),
                budget,
            )?;
            crate::relationship::catalog::validate(database, budget)?;
            let (catalog, row, properties) =
                crate::schema::properties::load(database, &definition, budget)?;
            let renamed = crate::schema::properties::rename(&properties, old, new, budget)?;
            let properties = if renamed == properties {
                None
            } else {
                Some((catalog.root(), row, renamed))
            };
            let relationships =
                crate::schema::catalog::table(database, b"MSysRelationships", budget)?;
            let fields = [
                (
                    crate::schema::catalog::column(&relationships, b"szObject")?,
                    crate::schema::catalog::column(&relationships, b"szColumn")?,
                ),
                (
                    crate::schema::catalog::column(&relationships, b"szReferencedObject")?,
                    crate::schema::catalog::column(&relationships, b"szReferencedColumn")?,
                ),
            ];
            let mut updates = Vec::new();
            let mut rows = database.rows(&relationships, budget)?;
            while let Some(mut row) = rows.next_row()? {
                for (object, field) in fields {
                    row.budget_mut().charge_work_units(2048)?;
                    if row
                        .field(object)
                        .and_then(|field| field.raw_bytes())
                        .is_some_and(|name| {
                            crate::catalog::name_key::catalog_names_equal_for(order, name, table)
                        })
                        && row
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
            let mut edited = crate::schema::definition::DefinitionEdit::new(&definition, budget)?;
            edited.columns[selected].name = new;
            let mut edits = PageEdits::new(database.geometry().page_count());
            edited.stage(database, &definition, &mut edits, budget)?;
            Ok((
                edits,
                (definition.root(), properties, relationships.root(), updates),
            ))
        })?;
    if let Some((catalog, row, bytes)) = properties {
        crate::schema::properties::store(file, journal, catalog, row, &bytes, budget)?;
    }
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
        let definition = database.table_definition(root, budget)?;
        crate::properties::value_policy::options(database, &definition, budget)?;
        crate::relationship::catalog::validate(database, budget)?;
        Ok((PageEdits::new(database.geometry().page_count()), ()))
    })
}
